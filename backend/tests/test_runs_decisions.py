import json

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.tenancy import complete_run


def _register(client: TestClient, username: str) -> None:
    response = client.post("/auth/register", json={"username": username, "password": "x"})
    assert response.status_code == 201, response.text


async def _complete_run_with_one_exception(
    client: TestClient, db_session_factory: async_sessionmaker[AsyncSession], username: str
) -> str:
    _register(client, username)
    run_id = str(client.post("/runs", json={"source": "demo"}).json()["id"])
    result_json = json.dumps(
        {
            "groups": [],
            "exceptions": [
                {
                    "id": "EXC-1",
                    "code": "MISSING_IN_BANK",
                    "severity": 1,
                    "amount_at_risk": 5000,
                    "records": [{"kind": "settlement", "id": "STL1"}],
                    "attempted": ["P1"],
                }
            ],
            "output_hash": "deadbeef",
        }
    )
    metrics_json = json.dumps(
        {
            "auto_rate": 0.0,
            "assist_rate": 0.0,
            "open_rate": 1.0,
            "records": 1,
            "open_exceptions": 1,
            "amount_at_risk": 5000,
            "throughput_rps": 0.0,
            "p50_ms": 0,
            "p95_ms": 0,
            "llm_requests": 0,
            "llm_tokens": 0,
            "llm_degraded": False,
            "output_hash": "deadbeef",
        }
    )
    async with db_session_factory() as db:
        await complete_run(db, run_id, result_json, metrics_json)
    return run_id


async def test_recording_a_decision_returns_it_and_lists_it(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _complete_run_with_one_exception(runs_client, db_session_factory, "approver")

    response = runs_client.post(
        f"/runs/{run_id}/exceptions/EXC-1/decision", json={"decision": "approved", "note": "confirmed with finance"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exception_id"] == "EXC-1"
    assert body["decision"] == "approved"

    listed = runs_client.get(f"/runs/{run_id}/decisions").json()
    assert len(listed) == 1
    assert listed[0]["decision"] == "approved"


async def test_a_second_decision_overwrites_the_first(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _complete_run_with_one_exception(runs_client, db_session_factory, "flip-flopper")

    runs_client.post(f"/runs/{run_id}/exceptions/EXC-1/decision", json={"decision": "approved"})
    runs_client.post(f"/runs/{run_id}/exceptions/EXC-1/decision", json={"decision": "rejected"})

    listed = runs_client.get(f"/runs/{run_id}/decisions").json()
    assert len(listed) == 1
    assert listed[0]["decision"] == "rejected"


async def test_decision_does_not_change_the_run_s_own_metrics(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _complete_run_with_one_exception(runs_client, db_session_factory, "immutable-check")
    before = runs_client.get(f"/runs/{run_id}").json()["metrics"]

    runs_client.post(f"/runs/{run_id}/exceptions/EXC-1/decision", json={"decision": "approved"})

    after = runs_client.get(f"/runs/{run_id}").json()["metrics"]
    assert before == after


def test_decision_on_another_user_s_run_is_404_not_403(runs_client: TestClient) -> None:
    _register(runs_client, "run-owner")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "attacker")
    response = runs_client.post(f"/runs/{run_id}/exceptions/EXC-1/decision", json={"decision": "approved"})

    assert response.status_code == 404
