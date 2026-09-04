"""The tools Lyra gained beyond "what happened in this one run"."""

import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.passwords import hash_password
from db.tenancy import complete_run, create_run, create_user, record_exception_decision
from llm.ask import citations, follow_ups, seed_history, tool_label
from llm.tools import TOOL_SCHEMAS, call_tool
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
                "evidence": [{"field": "narration", "value": "NEFT UTR HDFC0009912 PAYOUT", "source_id": "BNK1"}],
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
            },
            {
                "id": "EXC-2",
                "code": "MISSING_IN_BANK",
                "severity": 1,
                "amount_at_risk": 7000,
                "records": [{"kind": "settlement", "id": "STL3"}],
                "attempted": ["P1"],
            },
            {
                "id": "EXC-3",
                "code": "FEE_GST_DELTA_UNCONFIRMED",
                "severity": 2,
                "amount_at_risk": 100,
                "records": [{"kind": "payment", "id": "PAY9"}],
                "attempted": ["P2"],
            },
        ],
        "output_hash": "deadbeef",
    }
)


def _metrics(auto_rate: float, open_exceptions: int, amount: int) -> str:
    return json.dumps(
        {
            "auto_rate": auto_rate,
            "assist_rate": 0.0,
            "open_rate": 0.1,
            "records": 10,
            "open_exceptions": open_exceptions,
            "amount_at_risk": amount,
            "throughput_rps": 100.0,
            "p50_ms": 5,
            "p95_ms": 5,
            "llm_requests": 0,
            "llm_tokens": 0,
            "llm_degraded": False,
            "output_hash": "deadbeef",
        }
    )


FORECAST_JSON = json.dumps({"days": [], "unrecognised_cash": 0})


async def _user_with_runs(
    db_session_factory: async_sessionmaker[AsyncSession], username: str, count: int = 1
) -> tuple[str, list[str]]:
    run_ids = []
    async with db_session_factory() as db:
        user = await create_user(db, username, hash_password("x"))
        for index in range(count):
            run, _created = await create_run(db, user.id, "demo")
            await complete_run(db, run.id, RESULT_JSON, _metrics(0.9 - index * 0.1, 3, 12100), FORECAST_JSON)
            run_ids.append(run.id)
    return user.id, run_ids


async def test_list_runs_needs_no_run_id_and_stays_inside_the_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The one tool with no run to gate on. Its scoping is the repository
    call, one level up -- a second user's runs must not appear."""
    mine, my_runs = await _user_with_runs(db_session_factory, "tools-list-mine", count=2)
    _theirs, _their_runs = await _user_with_runs(db_session_factory, "tools-list-theirs", count=2)

    async with db_session_factory() as db:
        result = await call_tool("list_runs", {}, db, mine)

    assert isinstance(result, Ok)
    returned = {row["run_id"] for row in result.value["runs"]}
    assert returned == set(my_runs)
    assert result.value["total"] == 2
    assert result.value["runs"][0]["auto_rate"] is not None, "the headline figures ride along"


async def test_compare_runs_computes_the_delta_rather_than_leaving_it_to_the_model(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Subtraction is exactly what the grounding check would reject as an
    unsourced number, so the tool does the arithmetic."""
    user_id, runs = await _user_with_runs(db_session_factory, "tools-compare", count=2)

    async with db_session_factory() as db:
        result = await call_tool("compare_runs", {"run_id_a": runs[0], "run_id_b": runs[1]}, db, user_id)

    assert isinstance(result, Ok)
    assert result.value["a"]["auto_rate"] == 0.9
    assert result.value["b"]["auto_rate"] == 0.8
    assert abs(result.value["delta_b_minus_a"]["auto_rate"] - (-0.1)) < 1e-9


async def test_compare_runs_refuses_a_run_belonging_to_someone_else(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mine, my_runs = await _user_with_runs(db_session_factory, "tools-cmp-mine")
    _theirs, their_runs = await _user_with_runs(db_session_factory, "tools-cmp-theirs")

    async with db_session_factory() as db:
        result = await call_tool("compare_runs", {"run_id_a": my_runs[0], "run_id_b": their_runs[0]}, db, mine)

    assert isinstance(result, Err)
    assert "found for this user" in result.reason


async def test_summarise_exceptions_groups_by_code_largest_exposure_first(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, runs = await _user_with_runs(db_session_factory, "tools-summarise")

    async with db_session_factory() as db:
        result = await call_tool("summarise_exceptions", {"run_id": runs[0]}, db, user_id)

    assert isinstance(result, Ok)
    by_code = result.value["by_code"]
    assert [b["code"] for b in by_code] == ["MISSING_IN_BANK", "FEE_GST_DELTA_UNCONFIRMED"]
    assert by_code[0]["count"] == 2
    assert by_code[0]["amount_at_risk"] == 12000
    assert result.value["total_exceptions"] == 3
    assert result.value["total_amount_at_risk"] == 12100


async def test_search_finds_a_utr_inside_the_evidence_the_engine_quoted(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The question "what happened to HDFC0009912" is the one a person
    actually asks, and the answer lives in evidence text, not in an id."""
    user_id, runs = await _user_with_runs(db_session_factory, "tools-search")

    async with db_session_factory() as db:
        hit = await call_tool("search_records", {"run_id": runs[0], "text": "hdfc0009912"}, db, user_id)
        miss = await call_tool("search_records", {"run_id": runs[0], "text": "nothing-like-this"}, db, user_id)

    assert isinstance(hit, Ok)
    assert hit.value["matched_groups_total"] == 1
    assert hit.value["matched_groups"][0]["id"] == "STL1"
    assert isinstance(miss, Ok)
    assert miss.value["matched_groups_total"] == 0
    assert miss.value["exceptions_total"] == 0


async def test_search_matches_an_exception_by_its_record_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, runs = await _user_with_runs(db_session_factory, "tools-search-exc")
    async with db_session_factory() as db:
        result = await call_tool("search_records", {"run_id": runs[0], "text": "STL3"}, db, user_id)
    assert isinstance(result, Ok)
    assert [e["id"] for e in result.value["exceptions"]] == ["EXC-2"]


async def test_get_dataset_says_a_seeded_run_carries_a_truth_file(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Whether accuracy can be scored at all is a real question about a run,
    and the honest answer for a seeded corpus differs from an uploaded one."""
    user_id, runs = await _user_with_runs(db_session_factory, "tools-dataset")
    async with db_session_factory() as db:
        result = await call_tool("get_dataset", {"run_id": runs[0]}, db, user_id)
    assert isinstance(result, Ok)
    assert result.value["dataset"] is None
    assert result.value["has_truth_file"] is True


async def test_get_decisions_returns_what_a_human_already_settled(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, runs = await _user_with_runs(db_session_factory, "tools-decisions")
    async with db_session_factory() as db:
        await record_exception_decision(db, runs[0], user_id, "EXC-1", "approve", note="cleared with the bank")
    async with db_session_factory() as db:
        result = await call_tool("get_decisions", {"run_id": runs[0]}, db, user_id)

    assert isinstance(result, Ok)
    assert result.value["decisions"][0]["exception_id"] == "EXC-1"
    assert result.value["decisions"][0]["note"] == "cleared with the bank"


async def test_every_declared_tool_is_actually_dispatchable(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A tool the model is offered but call_tool does not handle is worse than
    no tool: it burns a hop and comes back "unknown tool"."""
    user_id, runs = await _user_with_runs(db_session_factory, "tools-coverage")
    required_args = {
        "get_record": {"kind": "settlement", "id": "STL1"},
        "search_records": {"text": "STL1"},
        "compare_runs": {"run_id_a": runs[0], "run_id_b": runs[0]},
    }

    async with db_session_factory() as db:
        for schema in TOOL_SCHEMAS:
            args = {"run_id": runs[0], **required_args.get(schema["name"], {})}
            result = await call_tool(schema["name"], args, db, user_id)
            assert isinstance(result, Ok), f"{schema['name']} failed: {getattr(result, 'reason', '')}"


# --- the conversational pieces, which need no database -----------------------


def test_prior_turns_are_replayed_in_order_with_the_new_question_last() -> None:
    entries = seed_history("and the biggest one?", [("you", "what is open?"), ("lyra", "24 exceptions.")])
    assert [(e.role, e.text) for e in entries] == [
        ("user", "what is open?"),
        ("model", "24 exceptions."),
        ("user", "and the biggest one?"),
    ]


def test_a_long_conversation_is_trimmed_not_sent_whole() -> None:
    prior = [("you" if n % 2 == 0 else "lyra", f"turn {n}") for n in range(40)]
    entries = seed_history("now what?", prior)
    assert len(entries) == 13, "twelve remembered turns plus the new question"
    assert entries[-1].text == "now what?"
    assert entries[0].text == "turn 28", "the most recent turns are the ones kept"


def test_a_citation_is_only_offered_for_an_id_a_tool_actually_returned() -> None:
    """The chips are held to the same standard as the numbers: invented ids
    appear in no payload, so they get no link."""
    payloads = [{"invoice_ids": ["INV1"], "bank_line_id": "BNK1"}]
    cited = citations("INV1 tied to BNK1, unlike INV-FAKE-9.", payloads)
    assert [c["id"] for c in cited] == ["INV1", "BNK1"]
    assert all(c["id"] != "INV-FAKE-9" for c in cited)


def test_citations_are_ordered_as_the_answer_mentions_them() -> None:
    payloads = [{"invoice_ids": ["INV1", "INV2"]}]
    cited = citations("INV2 came before INV1 here.", payloads)
    assert [c["id"] for c in cited] == ["INV2", "INV1"]


def test_follow_ups_are_keyed_to_the_last_tool_used_and_never_empty() -> None:
    assert "How does this compare to my last run?" in follow_ups(["get_metrics"])
    assert len(follow_ups([])) > 0, "an answer with no tool calls still offers somewhere to go"
    assert len(follow_ups(["get_metrics", "summarise_exceptions"])) <= 3


def test_a_search_step_says_what_it_is_searching_for() -> None:
    assert tool_label("search_records", {"text": "HDFC001"}) == "Searching this run for 'HDFC001'"
    assert tool_label("get_metrics") == "Reading the scoreboard"


def test_every_tool_has_a_label_for_the_waiting_user() -> None:
    from llm.ask import TOOL_LABELS

    missing = [s["name"] for s in TOOL_SCHEMAS if s["name"] not in TOOL_LABELS]
    assert not missing, f"these tools would show a raw function name while someone waits: {missing}"
