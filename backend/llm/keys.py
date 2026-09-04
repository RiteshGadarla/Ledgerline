import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count


@dataclass(frozen=True)
class ApiKey:
    """One Gemini credential plus the stable, non-secret id its quota counters
    are kept under.

    The id is a digest of the key itself, so a key keeps its counters across
    restarts and across a reordering of GEMINI_API_KEY -- which matters because
    the per-day counters live in Redis until UTC midnight and must not be
    reshuffled by a deploy that happens to list the keys differently.
    """

    value: str
    id: str

    def __repr__(self) -> str:
        # A credential must never reach a log line, a traceback, or a repr of
        # the pool holding it. The id is safe: it is a one-way digest.
        return f"ApiKey(id={self.id!r})"


UNKEYED = ApiKey(value="", id="unkeyed")
"""The single slot an empty pool governs, so a keyless deployment (which
degrades to FakeClient/ScriptedAskClient) is still counted exactly as it was
before keys were pooled."""


@dataclass
class KeyPool:
    """Every configured Gemini credential, handed out round-robin.

    Free-tier quota is per key, so N keys are N independent RPM/RPD buckets
    rather than one shared ceiling. Two things have to agree for that to be
    true: the governor counts each key separately (llm/governor.py), and calls
    are spread across keys instead of hammering the first one. This pool is
    what spreads them -- `rotation()` starts one key further along each time,
    so consecutive calls use different credentials and a key that is out of
    quota is stepped over rather than retried.
    """

    keys: tuple[ApiKey, ...] = ()
    _turn: Iterator[int] = field(default_factory=count, init=False, repr=False)

    @classmethod
    def parse(cls, raw: str | None) -> "KeyPool":
        """Read a comma-separated GEMINI_API_KEY into a pool of any size.

        One key is the ordinary case and behaves exactly as a single key
        always did; blanks and duplicates are dropped so a trailing comma or
        a pasted repeat cannot silently halve the quota a pool appears to
        have.
        """
        seen: set[str] = set()
        keys: list[ApiKey] = []
        for candidate in (raw or "").split(","):
            value = candidate.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            keys.append(ApiKey(value=value, id=hashlib.sha256(value.encode()).hexdigest()[:8]))
        return cls(keys=tuple(keys))

    def __len__(self) -> int:
        return len(self.keys)

    def __bool__(self) -> bool:
        return bool(self.keys)

    def rotation(self) -> tuple[ApiKey, ...]:
        """Every key, ordered from the next one due a turn.

        The caller walks this in order and uses the first key that has quota
        left, so the tuple is both the rotation and the failover chain. An
        empty pool yields the unkeyed placeholder rather than nothing, so a
        caller never has to special-case "no keys configured".
        """
        if not self.keys:
            return (UNKEYED,)
        offset = next(self._turn) % len(self.keys)
        return self.keys[offset:] + self.keys[:offset]

    def next_key(self) -> ApiKey | None:
        """The key whose turn it is, or None when none are configured."""
        return self.rotation()[0] if self.keys else None
