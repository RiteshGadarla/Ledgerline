import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.tenancy import (
    get_dataset_for_user,
    get_run_for_user,
    list_dataset_files,
    list_exception_decisions,
    list_runs_for_user,
)
from engine.pipeline import deserialize_match_result
from money.result import Err, Ok, Result

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_metrics",
        "description": "Get the scoreboard metrics (auto rate, assist rate, false matches, rupees at risk, "
        "output hash, etc.) for one run.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "query_matches",
        "description": "List matched chains (invoice/payment/settlement/bank-line groups) for one run, "
        "optionally filtered by status.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string", "enum": ["auto", "assisted", "open"]},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "query_exceptions",
        "description": "List individual open exceptions for one run, optionally filtered by "
        "exception code, largest by rupees at risk first. The row list is capped, so never answer "
        "a 'how many' question by counting rows -- 'total' is the true count, and get_metrics "
        "carries the same figure as open_exceptions.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "code": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "get_record",
        "description": "Look up whether one record id (an invoice, payment, settlement, or bank line) from a "
        "run ended up in a matched group or an open exception, and which one.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["invoice", "payment", "settlement", "bank_line"]},
                "id": {"type": "string"},
            },
            "required": ["run_id", "kind", "id"],
        },
    },
    {
        "name": "get_forecast",
        "description": "Get the 14-day cash position projection for one run.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    # --- Tools that reach beyond the single run being viewed. ---------------
    #
    # The five above answer "what happened here". These answer the questions a
    # finance lead actually asks next: is this run better or worse than the
    # last one, what is this corpus, where is the record I care about, and has
    # anyone already dealt with it. Each still resolves tenancy per run id in
    # call_tool, so widening the surface does not widen what a user can see.
    {
        "name": "list_runs",
        "description": "List this user's recent runs, newest first, with each one's dataset, state and "
        "headline metrics. Takes no run_id -- use it to find a run, or to answer questions that "
        "compare runs or ask which one went best.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many runs to return (default 10)."}},
            "required": [],
        },
    },
    {
        "name": "compare_runs",
        "description": "Compare two runs metric by metric, with the delta between them. Use this rather than "
        "calling get_metrics twice when the question is about a difference, a regression or an improvement.",
        "parameters": {
            "type": "object",
            "properties": {"run_id_a": {"type": "string"}, "run_id_b": {"type": "string"}},
            "required": ["run_id_a", "run_id_b"],
        },
    },
    {
        "name": "search_records",
        "description": "Free-text search across one run's matched chains and open exceptions: record ids, "
        "exception codes, and the evidence text (bank narrations, UTRs) the engine matched on. Use it when "
        "the question names something -- a UTR, a narration fragment, part of an id -- rather than asking "
        "for a category.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "text": {"type": "string", "description": "Substring to look for; case-insensitive."},
            },
            "required": ["run_id", "text"],
        },
    },
    {
        "name": "get_dataset",
        "description": "Describe the corpus behind a run: its name, whether it was generated or uploaded, its "
        "size and seed, and which source files it holds. Use it to answer what the run was actually reading, "
        "and whether its accuracy figures have a truth file to be scored against.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "summarise_exceptions",
        "description": "Group one run's open exceptions by code, with a count and the rupees at risk for each, "
        "largest exposure first. The cheap way to answer 'what is broken' or 'where is the money stuck' "
        "without listing individual rows.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "get_decisions",
        "description": "List the decisions a human has already recorded against this run's exceptions "
        "(who cleared or wrote off what, and any note). Use it before suggesting an action, so an item "
        "somebody has already dealt with is not raised again.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
]


# An uncapped list tool is a liability for any model and fatal for a small
# local one: a 1,276-record run returns ~8.5k tokens of matched chains, which
# a CPU-served model spends minutes reading and then answers badly from. The
# cap keeps the payload bounded while `total` preserves the honest count, so
# "how many are there" is answered from a number rather than by counting rows.
LLM_TOOL_MAX_ROWS = int(os.environ.get("LLM_TOOL_MAX_ROWS", "20"))


def _capped(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    shown = rows[:LLM_TOOL_MAX_ROWS]
    payload: dict[str, Any] = {key: shown, "total": len(rows)}
    if len(rows) > len(shown):
        payload["truncated"] = True
    return payload


async def call_tool(name: str, args: dict[str, Any], db: AsyncSession, user_id: str) -> Result[dict[str, Any]]:
    """The single dispatch point for every ask-agent tool call. Every branch
    re-verifies tenancy via get_run_for_user(db, run_id, user_id) regardless
    of what run_id the model supplied -- a foreign run_id is Err("not found")
    from this repository-layer check, never something the model could argue
    its way around.
    """
    # Tools that are about the account rather than one run. They are dispatched
    # before the run_id gate because they have no run to gate on -- but they
    # are still scoped to `user_id` by the repository call inside, which is the
    # same boundary, applied one level up.
    if name == "list_runs":
        return await _list_runs(args, db, user_id)
    if name == "compare_runs":
        return await _compare_runs(args, db, user_id)

    run_id = args.get("run_id")
    if not isinstance(run_id, str):
        return Err("run_id is required")

    run = await get_run_for_user(db, run_id, user_id)
    if run is None:
        return Err(f"no run {run_id!r} found for this user")

    if name == "get_metrics":
        if run.metrics_json is None:
            return Err(f"run {run_id!r} has no metrics yet (state={run.state})")
        return Ok({"run_id": run_id, "metrics": _loads(run.metrics_json)})

    if name == "get_forecast":
        if run.forecast_json is None:
            return Err(f"run {run_id!r} has no forecast yet (state={run.state})")
        return Ok({"run_id": run_id, "forecast": _loads(run.forecast_json)})

    if name == "get_dataset":
        return await _get_dataset(run, db, user_id)

    if name == "get_decisions":
        decisions = await list_exception_decisions(db, run_id, user_id)
        if decisions is None:
            return Err(f"no run {run_id!r} found for this user")
        return Ok(
            {
                "run_id": run_id,
                **_capped(
                    [
                        {
                            "exception_id": d.exception_id,
                            "decision": d.decision,
                            "note": d.note,
                            "decided_at": d.created_at.isoformat(),
                        }
                        for d in decisions
                    ],
                    "decisions",
                ),
            }
        )

    if name in ("query_matches", "query_exceptions", "get_record", "search_records", "summarise_exceptions"):
        if run.result_json is None:
            return Err(f"run {run_id!r} has no result yet (state={run.state})")
        result = deserialize_match_result(run.result_json)

        if name == "query_matches":
            status = args.get("status")
            groups = [g for g in result.groups if status is None or g.status == status]
            return Ok({"run_id": run_id, **_capped([g.model_dump(mode="json") for g in groups], "groups")})

        if name == "query_exceptions":
            code = args.get("code")
            exceptions = [e for e in result.exceptions if code is None or e.code.value == code]
            # Largest exposure first, so what survives the cap is what matters
            # -- the same order the exceptions surface uses.
            exceptions = sorted(exceptions, key=lambda e: e.amount_at_risk, reverse=True)
            return Ok({"run_id": run_id, **_capped([e.model_dump(mode="json") for e in exceptions], "exceptions")})

        if name == "summarise_exceptions":
            # One row per code rather than per exception. A run with 207 open
            # items across 4 codes is 4 lines here, which is both the honest
            # shape of the answer and the difference between a payload the
            # model reads and one it truncates.
            buckets: dict[str, dict[str, Any]] = {}
            for exc in result.exceptions:
                bucket = buckets.setdefault(
                    exc.code.value, {"code": exc.code.value, "count": 0, "amount_at_risk": 0, "example_ids": []}
                )
                bucket["count"] += 1
                bucket["amount_at_risk"] += exc.amount_at_risk
                if len(bucket["example_ids"]) < 3:
                    bucket["example_ids"].append(exc.id)
            rows = sorted(buckets.values(), key=lambda b: b["amount_at_risk"], reverse=True)
            return Ok(
                {
                    "run_id": run_id,
                    "by_code": rows,
                    "total_exceptions": len(result.exceptions),
                    "total_amount_at_risk": sum(e.amount_at_risk for e in result.exceptions),
                }
            )

        if name == "search_records":
            needle = args.get("text")
            if not isinstance(needle, str) or not needle.strip():
                return Err("text is required")
            return Ok({"run_id": run_id, "query": needle, **_search(result, needle)})

        # get_record
        kind, record_id = args.get("kind"), args.get("id")
        if not isinstance(kind, str) or not isinstance(record_id, str):
            return Err("kind and id are required")
        for group in result.groups:
            ids_for_kind = {
                "invoice": group.invoice_ids,
                "payment": group.payment_ids,
                "settlement": [group.settlement_id] if group.settlement_id else [],
                "bank_line": [group.bank_line_id] if group.bank_line_id else [],
            }.get(kind, [])
            if record_id in ids_for_kind:
                return Ok({"run_id": run_id, "found_in": "matched_group", "group": group.model_dump(mode="json")})
        for exc in result.exceptions:
            if any(r.kind == kind and r.id == record_id for r in exc.records):
                return Ok({"run_id": run_id, "found_in": "exception", "exception": exc.model_dump(mode="json")})
        return Err(f"{kind} {record_id!r} was not found in run {run_id!r}'s matches or exceptions")

    return Err(f"unknown tool {name!r}")


def _loads(raw: str) -> Any:
    import json

    return json.loads(raw)


def _metrics_of(run: Any) -> dict[str, Any] | None:
    return _loads(run.metrics_json) if run.metrics_json else None


# The figures worth putting side by side. Deliberately not every field in
# RunMetrics: a comparison the model has to summarise is a comparison it can
# get wrong, and these are the ones a run is actually judged on.
_COMPARABLE = (
    "auto_rate",
    "assist_rate",
    "open_rate",
    "precision",
    "recall",
    "false_matches",
    "records",
    "open_exceptions",
    "amount_at_risk",
    "throughput_rps",
    "llm_requests",
    "llm_tokens",
)


async def _list_runs(args: dict[str, Any], db: AsyncSession, user_id: str) -> Result[dict[str, Any]]:
    raw_limit = args.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) and 0 < raw_limit <= 50 else 10
    runs = await list_runs_for_user(db, user_id, limit=limit)

    rows = []
    for run in runs:
        metrics = _metrics_of(run)
        rows.append(
            {
                "run_id": run.id,
                "state": run.state,
                "source": run.source,
                "size": run.size,
                "seed": run.seed,
                "created_at": run.created_at.isoformat(),
                # Only the headline figures: this is a tool for finding a run,
                # and a full metrics block per row would blow the payload on
                # the way to a question that is really about one of them.
                "auto_rate": (metrics or {}).get("auto_rate"),
                "open_exceptions": (metrics or {}).get("open_exceptions"),
                "amount_at_risk": (metrics or {}).get("amount_at_risk"),
            }
        )
    return Ok(_capped(rows, "runs"))


async def _compare_runs(args: dict[str, Any], db: AsyncSession, user_id: str) -> Result[dict[str, Any]]:
    a_id, b_id = args.get("run_id_a"), args.get("run_id_b")
    if not isinstance(a_id, str) or not isinstance(b_id, str):
        return Err("run_id_a and run_id_b are required")

    pair = {}
    for label, run_id in (("a", a_id), ("b", b_id)):
        run = await get_run_for_user(db, run_id, user_id)
        if run is None:
            return Err(f"no run {run_id!r} found for this user")
        metrics = _metrics_of(run)
        if metrics is None:
            return Err(f"run {run_id!r} has no metrics yet (state={run.state})")
        pair[label] = metrics

    # The delta is computed here rather than left to the model. Subtraction is
    # exactly the kind of arithmetic the grounding check would reject as an
    # unsourced number, and exactly the kind a model gets subtly wrong.
    deltas = {}
    for field in _COMPARABLE:
        left, right = pair["a"].get(field), pair["b"].get(field)
        if isinstance(left, int | float) and isinstance(right, int | float):
            deltas[field] = right - left

    return Ok(
        {
            "run_id_a": a_id,
            "run_id_b": b_id,
            "a": {f: pair["a"].get(f) for f in _COMPARABLE},
            "b": {f: pair["b"].get(f) for f in _COMPARABLE},
            "delta_b_minus_a": deltas,
        }
    )


async def _get_dataset(run: Any, db: AsyncSession, user_id: str) -> Result[dict[str, Any]]:
    if run.dataset_id is None:
        # A seeded run has no Dataset row; its corpus is reproduced from the
        # seed instead. Saying so is a real answer, not a failure.
        return Ok(
            {
                "run_id": run.id,
                "dataset": None,
                "source": run.source,
                "seed": run.seed,
                "size": run.size,
                "mutations": run.mutations,
                # No dataset row means the corpus was generated from the seed
                # rather than uploaded, and a generated corpus always ships
                # with the answer key its accuracy is scored against.
                "has_truth_file": True,
                "note": "This run was generated from a seed rather than a saved dataset, "
                "so it is reproducible from that seed and carries a truth file.",
            }
        )

    dataset = await get_dataset_for_user(db, run.dataset_id, user_id)
    if dataset is None:
        return Err(f"the dataset behind run {run.id!r} is no longer available")

    files = await list_dataset_files(db, dataset.id)
    return Ok(
        {
            "run_id": run.id,
            "dataset": {
                "id": dataset.id,
                "name": dataset.name,
                "source": dataset.source,
                "status": dataset.status,
                "seed": dataset.seed,
                "size": dataset.size,
                "created_at": dataset.created_at.isoformat(),
            },
            # An uploaded corpus has no answer key, which is why precision and
            # recall are absent from its scoreboard. The model is told that
            # here so it can say so rather than reporting a missing figure as
            # a bad one.
            "has_truth_file": dataset.truth_json is not None,
            "files": [
                {
                    "role": f.role,
                    "filename": f.raw_filename,
                    "rows": f.row_count,
                    "valid_rows": f.valid_count,
                }
                for f in files
            ],
        }
    )


# Evidence values are short (a narration, a UTR); ids are shorter still. A
# generous cap on what is scanned per record keeps a pathological corpus from
# turning one search into a long CPU pause on the event loop.
_SEARCH_FIELD_LIMIT = 400


def _search(result: Any, needle: str) -> dict[str, Any]:
    """Substring search over the text a person would actually search by.

    Ids, exception codes and evidence values -- which is where narrations and
    UTRs live once the engine has quoted them. Case-insensitive, because
    nobody types a UTR in the case the bank wrote it.
    """
    lowered = needle.strip().lower()

    groups = []
    for group in result.groups:
        haystack = [group.id, group.settlement_id or "", group.bank_line_id or ""]
        haystack.extend(group.invoice_ids)
        haystack.extend(group.payment_ids)
        haystack.extend(e.value[:_SEARCH_FIELD_LIMIT] for e in group.evidence)
        if any(lowered in field.lower() for field in haystack if field):
            groups.append(group.model_dump(mode="json"))

    exceptions = []
    for exc in result.exceptions:
        haystack = [exc.id, exc.code.value, exc.explanation or "", exc.suggested_action or ""]
        haystack.extend(r.id for r in exc.records)
        if any(lowered in field.lower() for field in haystack if field):
            exceptions.append(exc.model_dump(mode="json"))

    # Two independent caps, each with its own honest total, rather than one
    # merged payload -- "12 matches and 3 exceptions mention this UTR" is the
    # answer, and a single `total` spanning both would be neither figure.
    return {
        "matched_groups": groups[:LLM_TOOL_MAX_ROWS],
        "matched_groups_total": len(groups),
        "matched_groups_truncated": len(groups) > LLM_TOOL_MAX_ROWS,
        "exceptions": exceptions[:LLM_TOOL_MAX_ROWS],
        "exceptions_total": len(exceptions),
        "exceptions_truncated": len(exceptions) > LLM_TOOL_MAX_ROWS,
    }
