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
        "description": "List open exceptions for one run, optionally filtered by exception code.",
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
            return Ok({"run_id": run_id, "groups": [g.model_dump(mode="json") for g in groups]})

        if name == "query_exceptions":
            code = args.get("code")
            exceptions = [e for e in result.exceptions if code is None or e.code.value == code]
            return Ok({"run_id": run_id, "exceptions": [e.model_dump(mode="json") for e in exceptions]})

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
