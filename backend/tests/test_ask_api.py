import json

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.routers.ask import get_ask_client
from db.tenancy import complete_run
from llm.ask import AskToolCall, AskTurn, ScriptedAskClient

METRICS_JSON = json.dumps(
    {
        "auto_rate": 0.5,
        "assist_rate": 0.0,
        "open_rate": 0.5,
        "records": 2,
        "open_exceptions": 1,
        "amount_at_risk": 100,
        "throughput_rps": 1.0,
        "p50_ms": 1,
        "p95_ms": 1,
        "llm_requests": 0,
        "llm_tokens": 0,
        "llm_degraded": False,
        "output_hash": "deadbeef",
    }
)
RESULT_JSON = json.dumps({"groups": [], "exceptions": [], "output_hash": "deadbeef"})


def _register(client: TestClient, username: str) -> None:
    response = client.post("/auth/register", json={"username": username, "password": "x"})
    assert response.status_code == 201, response.text


def _override_ask_client(run_id: str) -> None:
    from app.main import app

    def _factory() -> ScriptedAskClient:
        return ScriptedAskClient(
            turns=[
                AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
                AskTurn(text="The auto rate is 0.5."),
            ]
        )

    app.dependency_overrides[get_ask_client] = _factory


async def test_ask_endpoint_answers_from_tool_results(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _register(runs_client, "ask-endpoint-user")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]
    async with db_session_factory() as db:
        await complete_run(db, run_id, RESULT_JSON, METRICS_JSON, None)

    _override_ask_client(run_id)

    response = runs_client.post("/ask", json={"run_id": run_id, "question": "What's the auto rate?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "0.5" in body["answer"]
    assert body["degraded"] is False


def test_ask_with_an_exhausted_client_degrades_gracefully(runs_client: TestClient) -> None:
    """ScriptedAskClient(turns=[]) is exactly what get_ask_client falls back
    to when GEMINI_API_KEY isn't set (see llm/ask.py's ScriptedAskClient and
    app/routers/ask.py's fallback) -- overridden explicitly here so the test
    doesn't depend on whether this environment happens to have a real key."""
    from app.main import app

    app.dependency_overrides[get_ask_client] = lambda: ScriptedAskClient(turns=[])

    _register(runs_client, "ask-no-key-user")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]

    response = runs_client.post("/ask", json={"run_id": run_id, "question": "Anything?"})

    assert response.status_code == 200, response.text
    assert response.json()["degraded"] is True
