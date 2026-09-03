from dataclasses import dataclass
from typing import Literal

import redis.asyncio as redis
from pydantic import BaseModel

from ingest.headers import claimed_fields, deterministic_mapping
from ingest.signature import header_signature
from ingest.tabular import ParsedTable
from llm.gateway import LlmGateway
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from money.result import Err, Ok, Result

MAPPER_MODEL = PRIMARY_MODEL
MAPPER_SCHEMA_VERSION = "mapper-v1"
SAMPLE_ROW_COUNT = 5

SourceRole = Literal["ledger", "gateway", "settlement", "bank"]

CANONICAL_FIELDS: dict[SourceRole, list[str]] = {
    "ledger": ["id", "number", "customer", "amount", "issued_at", "ref"],
    "gateway": [
        "id",
        "order_id",
        "invoice_ref",
        "gross",
        "fee",
        "tax",
        "net",
        "status",
        "captured_at",
        "method",
        "settlement_id",
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
        # The version is in the key on purpose: these entries live for a
        # year, and an answer produced by an older mapper must not outlive
        # the logic that produced it.
        return f"mapping:{MAPPER_SCHEMA_VERSION}:{role}:{signature}"

    async def get(self, role: SourceRole, signature: str) -> MappingResponse | None:
        raw = await self.redis_client.get(self._key(role, signature))
        if raw is None:
            return None
        return MappingResponse.model_validate_json(raw)

    async def set(self, role: SourceRole, signature: str, mapping: MappingResponse) -> None:
        await self.redis_client.set(self._key(role, signature), mapping.model_dump_json(), ex=self.ttl_seconds)


def build_prompt(
    role: SourceRole, table: ParsedTable, unresolved: list[str] | None = None, taken: set[str] | None = None
) -> str:
    headers = unresolved if unresolved is not None else table.headers
    available = [f for f in CANONICAL_FIELDS[role] if f not in (taken or set())]
    lines = [
        f"Map these uploaded column headers to the canonical '{role}' schema fields: {', '.join(available)}.",
        f"Headers: {', '.join(headers)}",
        "Sample rows:",
    ]
    for row in table.rows[:SAMPLE_ROW_COUNT]:
        lines.append(str({header: row.get(header, "") for header in headers}))
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

    # What the table already knows is not a question for the model. Most
    # uploads resolve here completely, which costs nothing and cannot be
    # wrong in the way a guess can.
    resolved = deterministic_mapping(role, table.headers)
    taken = claimed_fields(resolved)
    unresolved = [header for header in table.headers if header not in resolved]

    fields = [
        FieldMapping(source_header=header, canonical_field=field, confidence=1.0) for header, field in resolved.items()
    ]

    if unresolved:
        prompt = build_prompt(role, table, unresolved=unresolved, taken=taken)
        result = await gateway.generate(
            model=MAPPER_MODEL,
            prompt=prompt,
            response_schema=MappingResponse,
            user_id=user_id,
            fallbacks=(BACKUP_MODEL,),
        )
        if isinstance(result, Err):
            return result
        proposed = MappingResponse.model_validate_json(result.value.raw_json)
        for field_mapping in proposed.fields:
            if field_mapping.source_header not in unresolved:
                continue  # the model answered about a column it was not asked about
            # A proposal that lands on a field the table already resolved is
            # dropped, not merged: the deterministic answer is the better one,
            # and two headers cannot both be the payout.
            if field_mapping.canonical_field in taken:
                fields.append(
                    FieldMapping(source_header=field_mapping.source_header, canonical_field=None, confidence=0.0)
                )
                continue
            if field_mapping.canonical_field is not None:
                taken.add(field_mapping.canonical_field)
            fields.append(field_mapping)

    # Back into the file's own column order, so the confirmation table the
    # uploader sees reads like the file they uploaded.
    by_header = {f.source_header: f for f in fields}
    ordered = [
        by_header.get(header, FieldMapping(source_header=header, canonical_field=None, confidence=0.0))
        for header in table.headers
    ]
    mapping = MappingResponse(fields=ordered)
    await cache.set(role, signature, mapping)
    return Ok(mapping)
