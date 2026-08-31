import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.passwords import hash_password
from db.tenancy import complete_run, create_run, create_user
from llm.tools import call_tool
from money.result import Err, Ok

RESULT_JSON = json.dumps(
    {
        "groups": [
            {
                "id": "STL1",
                "invoice_ids": ["INV1"],
                "payment_ids": ["PAY1"],
                "settlement_id": "STL1",
                "bank_line_id": "BNK1",
                "status": "auto",
                "pass_id": "P3",
                "confidence": 0.95,
                "residual": 0,
                "evidence": [],
            }
        ],
        "exceptions": [
            {
                "id": "EXC-1",
                "code": "MISSING_IN_BANK",
                "severity": 1,
                "amount_at_risk": 5000,
                "records": [{"kind": "settlement", "id": "STL2"}],
                "attempted": ["P1"],
            }
        ],
        "output_hash": "deadbeef",
    }
)
METRICS_JSON = json.dumps(
    {
        "auto_rate": 0.9,
        "assist_rate": 0.0,
        "open_rate": 0.1,
        "records": 10,
        "open_exceptions": 1,
        "amount_at_risk": 5000,
        "throughput_rps": 100.0,
        "p50_ms": 5,
        "p95_ms": 5,
        "llm_requests": 0,
        "llm_tokens": 0,
        "llm_degraded": False,
        "output_hash": "deadbeef",
    }
)
FORECAST_JSON = json.dumps({"days": [{"date": "2024-01-01", "recognised": 5000, "blocked": 0}], "unrecognised_cash": 0})


async def _completed_run(db_session_factory: async_sessionmaker[AsyncSession], username: str) -> tuple[str, str]:
    async with db_session_factory() as db:
        user = await create_user(db, username, hash_password("x"))
        run, _created = await create_run(db, user.id, "demo")
        await complete_run(db, run.id, RESULT_JSON, METRICS_JSON, FORECAST_JSON)
    return run.id, user.id


async def test_get_metrics_returns_the_runs_metrics(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-metrics")
    async with db_session_factory() as db:
        result = await call_tool("get_metrics", {"run_id": run_id}, db, user_id)
    assert isinstance(result, Ok)
    assert result.value["metrics"]["auto_rate"] == 0.9


async def test_get_forecast_returns_the_runs_forecast(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-forecast")
    async with db_session_factory() as db:
        result = await call_tool("get_forecast", {"run_id": run_id}, db, user_id)
    assert isinstance(result, Ok)
    assert result.value["forecast"]["unrecognised_cash"] == 0


async def test_query_matches_filters_by_status(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-matches")
    async with db_session_factory() as db:
        auto = await call_tool("query_matches", {"run_id": run_id, "status": "auto"}, db, user_id)
        assisted = await call_tool("query_matches", {"run_id": run_id, "status": "assisted"}, db, user_id)
    assert isinstance(auto, Ok)
    assert len(auto.value["groups"]) == 1
    assert isinstance(assisted, Ok)
    assert len(assisted.value["groups"]) == 0


async def test_query_exceptions_filters_by_code(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-exceptions")
    async with db_session_factory() as db:
        matching = await call_tool("query_exceptions", {"run_id": run_id, "code": "MISSING_IN_BANK"}, db, user_id)
        nonmatching = await call_tool(
            "query_exceptions", {"run_id": run_id, "code": "UNIDENTIFIED_CREDIT"}, db, user_id
        )
    assert isinstance(matching, Ok)
    assert len(matching.value["exceptions"]) == 1
    assert isinstance(nonmatching, Ok)
    assert len(nonmatching.value["exceptions"]) == 0


async def test_get_record_finds_a_record_in_a_matched_group(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-record-group")
    async with db_session_factory() as db:
        result = await call_tool("get_record", {"run_id": run_id, "kind": "invoice", "id": "INV1"}, db, user_id)
    assert isinstance(result, Ok)
    assert result.value["found_in"] == "matched_group"


async def test_get_record_finds_a_record_in_an_exception(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-record-exc")
    async with db_session_factory() as db:
        result = await call_tool("get_record", {"run_id": run_id, "kind": "settlement", "id": "STL2"}, db, user_id)
    assert isinstance(result, Ok)
    assert result.value["found_in"] == "exception"


async def test_get_record_not_found_is_an_err(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "tools-record-missing")
    async with db_session_factory() as db:
        result = await call_tool("get_record", {"run_id": run_id, "kind": "invoice", "id": "NOPE"}, db, user_id)
    assert isinstance(result, Err)


async def test_asking_about_another_users_run_id_is_not_found_from_the_tool_layer(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The tenancy test the plan calls for: the model can put whatever
    run_id it wants in a tool call's args, but the repository-layer check
    inside call_tool() is what actually decides -- never the model's own
    claim about which user it's acting for."""
    run_id, _owner_user_id = await _completed_run(db_session_factory, "tools-tenancy-owner")
    async with db_session_factory() as db:
        attacker = await create_user(db, "tools-tenancy-attacker", hash_password("y"))

    async with db_session_factory() as db:
        result = await call_tool("get_metrics", {"run_id": run_id}, db, attacker.id)

    assert isinstance(result, Err)
    assert "no run" in result.reason


async def test_missing_run_id_argument_is_an_err(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with db_session_factory() as db:
        user = await create_user(db, "tools-no-run-id", hash_password("x"))
        result = await call_tool("get_metrics", {}, db, user.id)
    assert isinstance(result, Err)
