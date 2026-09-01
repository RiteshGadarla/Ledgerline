from contracts.models import Exception_
from llm.client import LlmResponse
from llm.gateway import LlmGateway
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from llm.schemas import EXPLAIN_SCHEMA_VERSION, ExplanationResponse, cap_explanation
from money.result import Err, Result

EXPLAIN_MODEL = PRIMARY_MODEL


def build_prompt(exceptions: list[Exception_]) -> str:
    lines = ["Explain each exception below in one short sentence and suggest one concrete action.", ""]
    for exc in exceptions:
        lines.append(f"{exc.id} [{exc.code.value}] amount_at_risk={exc.amount_at_risk} paise")
    lines.append("")
    lines.append("Return JSON matching the schema, one item per exception_id listed above.")
    return "\n".join(lines)


async def explain(
    exceptions: list[Exception_], gateway: LlmGateway, user_id: str
) -> tuple[list[Exception_], int, int, bool]:
    """One batched call annotates every exception with an explanation and a
    suggested action, length-capped. Never blocks a run: on any failure the
    exceptions are returned unchanged, still carrying their template
    suggested_action from Phase 0's metadata table. The trailing bool is
    True whenever this call degraded (skipped or failed)."""
    if not exceptions:
        return exceptions, 0, 0, False

    prompt = build_prompt(exceptions)
    result: Result[LlmResponse] = await gateway.generate(
        model=EXPLAIN_MODEL,
        prompt=prompt,
        response_schema=ExplanationResponse,
        user_id=user_id,
        fallbacks=(BACKUP_MODEL,),
    )
    if isinstance(result, Err):
        return exceptions, 0, 0, True

    response = ExplanationResponse.model_validate_json(result.value.raw_json)
    by_id = {item.exception_id: item for item in response.items}

    annotated = []
    for exc in exceptions:
        item = by_id.get(exc.id)
        if item is None:
            annotated.append(exc)
            continue
        annotated.append(
            exc.model_copy(
                update={
                    "explanation": cap_explanation(item.explanation),
                    "suggested_action": cap_explanation(item.suggested_action) or exc.suggested_action,
                }
            )
        )
    return annotated, result.value.input_tokens, result.value.output_tokens, False


__all__ = ["EXPLAIN_MODEL", "EXPLAIN_SCHEMA_VERSION", "build_prompt", "explain"]
