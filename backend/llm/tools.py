import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.tenancy import get_run_for_user
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

    if name in ("query_matches", "query_exceptions", "get_record"):
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
