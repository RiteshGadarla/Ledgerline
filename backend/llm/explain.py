from collections.abc import Sequence

from contracts.models import Exception_
from llm.client import LlmResponse
from llm.gateway import LlmGateway
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from llm.schemas import EXPLAIN_SCHEMA_VERSION, ExplanationResponse, cap_explanation
from money.result import Err, Result

EXPLAIN_MODEL = PRIMARY_MODEL


def distinct_codes(exceptions: Sequence[Exception_]) -> list[str]:
    """The exception codes present, deduplicated and ordered.

    This is the whole optimisation. A run's open exceptions are many and its
    codes are few -- 88 exceptions across 4 codes, in the run this was
    measured on -- and the explanation the model writes depends only on the
    code. Sorted because the prompt built from this list is the cache key,
    and a key that depended on iteration order would never hit twice.
    """
    return sorted({exc.code.value for exc in exceptions})


def build_prompt(codes: Sequence[str]) -> str:
    """One line per code, and nothing else.

    Deliberately carries no ids, counts or amounts. The model demonstrably
    does not use them -- 42 exceptions with 42 different amounts came back
    with one byte-identical sentence -- so run-specific detail here would buy
    nothing and cost the cache: two runs that break in the same four ways
    produce the same prompt, and the second one is served from Redis without
    an API call at all.
    """
    lines = [
        "Explain each reconciliation exception type below in one short sentence,",
        "and suggest one concrete action for it. Write about the type of break,",
        "not about any particular record.",
        "",
    ]
    lines.extend(codes)
    lines.append("")
    lines.append("Return JSON matching the schema, one item per code listed above.")
    return "\n".join(lines)


async def explain(
    exceptions: list[Exception_], gateway: LlmGateway, user_id: str
) -> tuple[list[Exception_], int, int, bool]:
    """One call explains every code present, and each exception is annotated
    from its code's answer.

    Never blocks a run: on any failure the exceptions are returned unchanged,
    still carrying their template suggested_action from Phase 0's metadata
    table. A code the model declined to cover is left the same way, one
    exception at a time, rather than failing the batch. The trailing bool is
    True whenever this call degraded (skipped or failed).
    """
    if not exceptions:
        return exceptions, 0, 0, False

    prompt = build_prompt(distinct_codes(exceptions))
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
    # Normalised on the way in: the codes are the join key between what was
    # asked and what came back, and a model that answers "missing_in_bank"
    # for MISSING_IN_BANK has still answered the question.
    by_code = {item.code.strip().upper(): item for item in response.items}

    annotated = []
    for exc in exceptions:
        item = by_code.get(exc.code.value)
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


__all__ = ["EXPLAIN_MODEL", "EXPLAIN_SCHEMA_VERSION", "build_prompt", "distinct_codes", "explain"]
