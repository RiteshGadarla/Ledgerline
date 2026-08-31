import json

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.tenancy import complete_run


def _register(client: TestClient, username: str) -> None:
    response = client.post("/auth/register", json={"username": username, "password": "x"})
    assert response.status_code == 201, response.text


def test_create_run_returns_202_and_queued_state(runs_client: TestClient) -> None:
    _register(runs_client, "alice")

    response = runs_client.post("/runs", json={"source": "demo", "seed": 1001, "size": 50})

    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    assert body["source"] == "demo"
    assert body["metrics"] is None


def test_mutations_field_is_rejected(runs_client: TestClient) -> None:
    _register(runs_client, "bob")

    response = runs_client.post("/runs", json={"source": "demo", "mutations": ["swap_utr"]})

    assert response.status_code == 422


def test_replaying_the_same_idempotency_key_returns_the_same_run(runs_client: TestClient) -> None:
    _register(runs_client, "carol")

    first = runs_client.post("/runs", json={"source": "demo", "seed": 1001, "idempotency_key": "key-1"})
    second = runs_client.post("/runs", json={"source": "demo", "seed": 1001, "idempotency_key": "key-1"})

    assert first.json()["id"] == second.json()["id"]


def test_different_idempotency_key_creates_a_separate_run(runs_client: TestClient) -> None:
    _register(runs_client, "dave")

    first = runs_client.post("/runs", json={"source": "demo", "idempotency_key": "key-a"})
    second = runs_client.post("/runs", json={"source": "demo", "idempotency_key": "key-b"})

    assert first.json()["id"] != second.json()["id"]


def test_get_run_across_users_is_404_not_403(runs_client: TestClient) -> None:
    _register(runs_client, "erin")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "frank")
    response = runs_client.get(f"/runs/{run_id}")

    assert response.status_code == 404


def test_export_csv_across_users_is_404_not_403(runs_client: TestClient) -> None:
    _register(runs_client, "grace")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "heidi")
    response = runs_client.get(f"/runs/{run_id}/export.csv")

    assert response.status_code == 404


def test_export_csv_before_completion_is_404(runs_client: TestClient) -> None:
    _register(runs_client, "ivan")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]

    response = runs_client.get(f"/runs/{run_id}/export.csv")

    assert response.status_code == 404


async def test_stream_replays_terminal_state_immediately_without_a_worker(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A run that already reached a terminal state (as if a worker completed
    it before this client ever connected) must be visible on the very first
    SSE event, from the row alone -- no live pub/sub message required. This
    is exactly the "reconnect resumes from the run row" guarantee."""
    register_response = runs_client.post("/auth/register", json={"username": "judy", "password": "x"})
    run_response = runs_client.post("/runs", json={"source": "demo"})
    run_id = run_response.json()["id"]
    user_id = register_response.json()["id"]

    result_json = json.dumps({"groups": [], "exceptions": [], "output_hash": "deadbeef"})
    metrics_json = json.dumps(
        {
            "auto_rate": 1.0,
            "assist_rate": 0.0,
            "open_rate": 0.0,
            "records": 0,
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

    with runs_client.stream("GET", f"/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        first_line = next(response.iter_lines())
        assert first_line.startswith("data: ")
        payload = json.loads(first_line.removeprefix("data: "))
        assert payload["state"] == "complete"

    assert user_id  # sanity: registration succeeded


def test_list_runs_returns_only_the_caller_s_runs_most_recent_first(runs_client: TestClient) -> None:
    _register(runs_client, "karl")
    first_id = runs_client.post("/runs", json={"source": "demo", "seed": 1}).json()["id"]
    second_id = runs_client.post("/runs", json={"source": "demo", "seed": 2}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "liam")
    runs_client.post("/runs", json={"source": "demo", "seed": 3})
    other_user_runs = runs_client.get("/runs").json()
    assert len(other_user_runs) == 1

    runs_client.post("/auth/logout")
    runs_client.post("/auth/login", json={"username": "karl", "password": "x"})
    karl_runs = runs_client.get("/runs").json()
    assert [r["id"] for r in karl_runs] == [second_id, first_id]


async def test_get_run_result_returns_groups_and_exceptions(
    runs_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _register(runs_client, "mia")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]

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

    response = runs_client.get(f"/runs/{run_id}/result")

    assert response.status_code == 200
    body = response.json()
    assert body["output_hash"] == "deadbeef"
    assert len(body["exceptions"]) == 1
    assert body["exceptions"][0]["code"] == "MISSING_IN_BANK"


def test_get_run_result_before_completion_is_404(runs_client: TestClient) -> None:
    _register(runs_client, "noah")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]

    response = runs_client.get(f"/runs/{run_id}/result")

    assert response.status_code == 404


def test_get_run_result_across_users_is_404_not_403(runs_client: TestClient) -> None:
    _register(runs_client, "olivia")
    run_id = runs_client.post("/runs", json={"source": "demo"}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "paul")
    response = runs_client.get(f"/runs/{run_id}/result")

    assert response.status_code == 404
