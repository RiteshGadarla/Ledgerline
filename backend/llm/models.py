"""The model chain every LLM call in the system draws from.

One ordered list, in one place, so switching models is a single edit rather
than a hunt through the triage, explain and ask modules. The backup is tried
only when the primary cannot serve a request at all -- quota refused, rate
limited, or a transient provider failure that survived backoff -- never to
"improve" an answer the primary already gave.
"""

PRIMARY_MODEL = "gemma-4-31b-it"
BACKUP_MODEL = "gemma-4-26b-a4b-it"

# Tried in order. Everything that reserves budget or reads a rate limit keys
# off these names, so llm/limits.py must carry an entry for each.
MODEL_CHAIN: tuple[str, ...] = (PRIMARY_MODEL, BACKUP_MODEL)

__all__ = ["BACKUP_MODEL", "MODEL_CHAIN", "PRIMARY_MODEL"]
