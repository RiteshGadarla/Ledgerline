import hashlib


def normalise_header(header: str) -> str:
    return " ".join(header.strip().lower().split())


def header_signature(headers: list[str]) -> str:
    """sha256 of the normalised header sequence. Two uploads with the same
    columns in the same order always land on the same signature, so a schema
    mapping is only ever asked of the model once per distinct file shape."""
    normalised = [normalise_header(h) for h in headers]
    digest_input = "\x1f".join(normalised).encode()
    return hashlib.sha256(digest_input).hexdigest()
