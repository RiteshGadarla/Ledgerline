from dataclasses import dataclass
from typing import Literal

import redis.asyncio as redis
from pydantic import BaseModel

from ingest.signature import header_signature
from ingest.tabular import ParsedTable
from llm.gateway import LlmGateway
from money.result import Err, Ok, Result

MAPPER_MODEL = "gemini-3.5-flash-lite"
MAPPER_SCHEMA_VERSION = "mapper-v1"
SAMPLE_ROW_COUNT = 5

SourceRole = Literal["ledger", "gateway", "settlement", "bank"]

CANONICAL_FIELDS: dict[SourceRole, list[str]] = {
    "ledger": ["id", "number", "customer", "amount", "issued_at", "ref"],
    "gateway": [
        "id", "order_id", "invoice_ref", "gross", "fee", "tax", "net",
        "status", "captured_at", "method", "settlement_id",
    ],
    "settlement": ["id", "utr", "payout", "fees", "tax", "adjustments", "settled_at", "payment_ids"],
    "bank": ["id", "value_date", "narration", "credit", "debit", "balance"],
}


class FieldMapping(BaseModel):
    source_header: str
    canonical_field: str | None
    confidence: float


class MappingResponse(BaseModel):
    fields: list[FieldMapping]

    def canonical_for(self, source_header: str) -> str | None:
        for field in self.fields:
            if field.source_header == source_header:
                return field.canonical_field
        return None


@dataclass
class MappingCache:
    """Cached purely by header signature (+ role): sample row *values* never
    enter the cache key, since headers alone identify a file's shape and
    carry no user data -- which is the entire reason this can be cached
    globally rather than per-user."""

    redis_client: "redis.Redis"
    ttl_seconds: int = 365 * 24 * 3600

    def _key(self, role: SourceRole, signature: str) -> str:
        return f"mapping:{role}:{signature}"

    async def get(self, role: SourceRole, signature: str) -> MappingResponse | None:
        raw = await self.redis_client.get(self._key(role, signature))
        if raw is None:
            return None
        return MappingResponse.model_validate_json(raw)

    async def set(self, role: SourceRole, signature: str, mapping: MappingResponse) -> None:
        await self.redis_client.set(self._key(role, signature), mapping.model_dump_json(), ex=self.ttl_seconds)


def build_prompt(role: SourceRole, table: ParsedTable) -> str:
    lines = [
        f"Map these uploaded column headers to the canonical '{role}' schema fields: "
        f"{', '.join(CANONICAL_FIELDS[role])}.",
        f"Headers: {', '.join(table.headers)}",
        "Sample rows:",
    ]
    for row in table.rows[:SAMPLE_ROW_COUNT]:
        lines.append(str({header: row.get(header, "") for header in table.headers}))
    lines.append(
        "Return JSON matching the schema: one entry per header, canonical_field null if none fits, "
        "with a confidence in [0, 1]."
    )
    return "\n".join(lines)


async def map_schema(
    role: SourceRole,
    table: ParsedTable,
    gateway: LlmGateway,
    cache: MappingCache,
    user_id: str,
) -> Result[MappingResponse]:
    signature = header_signature(table.headers)
    cached = await cache.get(role, signature)
    if cached is not None:
        return Ok(cached)

    prompt = build_prompt(role, table)
    result = await gateway.generate(
        model=MAPPER_MODEL, prompt=prompt, response_schema=MappingResponse, user_id=user_id
    )
    if isinstance(result, Err):
        return result

    mapping = MappingResponse.model_validate_json(result.value.raw_json)
    await cache.set(role, signature, mapping)
    return Ok(mapping)
