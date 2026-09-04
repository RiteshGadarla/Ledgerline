import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.redis_client import get_redis
from app.settings import get_settings
from db.tenancy import UserRecord
from llm.ask import AskClient, GeminiAskClient, ScriptedAskClient, ask, ask_stream
from llm.governor import Governor
from llm.keys import KeyPool
from llm.limits import load_model_limits, user_daily_quota

router = APIRouter(prefix="/ask", tags=["ask"])


class AskTurnIn(BaseModel):
    """One prior turn of the conversation, replayed for context."""

    role: Literal["you", "lyra"]
    text: str


class AskRequest(BaseModel):
    # Optional, so Lyra can also be asked from outside a run surface: with no
    # run in hand her first move is list_runs, which is the honest way to
    # answer "which run went best?" rather than guessing which one was meant.
    run_id: str | None = None
    question: str
    # The transcript already lives in the browser and the agent keeps no state
    # between requests, so the client replays it. It is dialogue context only:
    # every number in the answer must still come from a tool result on this
    # turn, which the grounding check enforces whatever the history says.
    history: list[AskTurnIn] = Field(default_factory=list, max_length=40)


class AskResponseOut(BaseModel):
    answer: str
    degraded: bool


def get_ask_client(request: Request) -> AskClient:
    """A dependency (not a plain call) so tests can override it with a
    ScriptedAskClient carrying canned turns instead of ever reaching Gemini."""
    keys = KeyPool.parse(get_settings().gemini_api_key)
    if keys:
        return GeminiAskClient(keys)
    # No key configured: degrade immediately and consistently, same as
    # llm/factory.py's build_gateway() falling back to an empty FakeClient.
    return ScriptedAskClient(turns=[])


def get_ask_governor(request: Request) -> Governor:
    limits = load_model_limits()
    keys = KeyPool.parse(get_settings().gemini_api_key)
    return Governor(
        redis_client=get_redis(request),
        rpm_limits={model: limit.rpm for model, limit in limits.items()},
        rpd_limits={model: limit.rpd for model, limit in limits.items()},
        user_daily_quota=user_daily_quota(len(keys)),
        keys=keys,
    )


def _prior(payload: AskRequest) -> list[tuple[str, str]]:
    return [(turn.role, turn.text) for turn in payload.history]


@router.post("", response_model=AskResponseOut)
async def ask_endpoint(
    payload: AskRequest,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client: AskClient = Depends(get_ask_client),
    governor: Governor = Depends(get_ask_governor),
) -> AskResponseOut:
    """Answers a question about a run, grounded in that run's data. `degraded`
    is true when no model could be reached (quota exhausted or all attempts
    failed) and a fallback answer was returned instead."""
    answer = await ask(
        payload.question, payload.run_id, user.id, db, client, governor, prior=_prior(payload)
    )
    return AskResponseOut(answer=answer.text, degraded=answer.degraded)


@router.post(
    "/stream",
    response_class=StreamingResponse,
    response_description="text/event-stream of {type, ...} frames; render only the final 'done' event's answer.",
)
async def ask_stream_endpoint(
    payload: AskRequest,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client: AskClient = Depends(get_ask_client),
    governor: Governor = Depends(get_ask_governor),
) -> StreamingResponse:
    """The same answer, delivered as it is written.

    Server-sent events over POST rather than EventSource, because the
    question belongs in a body and not in a URL. The frame format is the
    same one the run stream uses, so the Next.js proxy passes it through
    untouched.
    """

    async def frames() -> AsyncIterator[bytes]:
        async for event in ask_stream(
            payload.question, payload.run_id, user.id, db, client, governor, prior=_prior(payload)
        ):
            yield f"data: {json.dumps(event)}\n\n".encode()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Proxies that buffer would defeat the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )
