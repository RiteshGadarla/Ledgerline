from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# No length, character-class, or complexity rules by design (Phase 9 spec):
# the only rejections are an empty username or password, enforced by the
# caller before this module is ever reached. A weak-but-remembered password
# beats a strong-but-written-on-a-sticky-note one; argon2id's cost is the
# actual defence.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
