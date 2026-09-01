import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.redis_client import get_redis
from app.settings import get_settings
from db.tenancy import UserRecord
from llm.ask import AskClient, GeminiAskClient, ScriptedAskClient, ask, ask_stream
from llm.governor import Governor
from llm.limits import DEFAULT_USER_DAILY_QUOTA, load_model_limits

router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    run_id: str
    question: str


class AskResponseOut(BaseModel):
    answer: str
    degraded: bool


def get_ask_client(request: Request) -> AskClient:
    """A dependency (not a plain call) so tests can override it with a
    ScriptedAskClient carrying canned turns instead of ever reaching Gemini."""
    api_key = get_settings().gemini_api_key
    if api_key:
        return GeminiAskClient(api_key)
    # No key configured: degrade immediately and consistently, same as
    # llm/factory.py's build_gateway() falling back to an empty FakeClient.
    return ScriptedAskClient(turns=[])


def get_ask_governor(request: Request) -> Governor:
    limits = load_model_limits()
    return Governor(
        redis_client=get_redis(request),
        rpm_limits={model: limit.rpm for model, limit in limits.items()},
        rpd_limits={model: limit.rpd for model, limit in limits.items()},
        user_daily_quota=DEFAULT_USER_DAILY_QUOTA,
    )


@router.post("", response_model=AskResponseOut)
async def ask_endpoint(
    payload: AskRequest,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client: AskClient = Depends(get_ask_client),
    governor: Governor = Depends(get_ask_governor),
) -> AskResponseOut:
    answer = await ask(payload.question, payload.run_id, user.id, db, client, governor)
    return AskResponseOut(answer=answer.text, degraded=answer.degraded)


@router.post("/stream")
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
        async for event in ask_stream(payload.question, payload.run_id, user.id, db, client, governor):
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
