"""API key generation and verification: a hash-at-rest scheme like password hashing, but one-way only -
these are machine credentials checked by comparison, never by a human typing them back in."""
from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIX = "lp_live_"
PREFIX_LEN = 16  # stored in the clear so the UI can tell keys apart without ever re-displaying the secret


def generate_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_prefix, key_hash). The raw key is shown to the caller exactly once, at creation -
    only its hash is ever persisted."""
    raw = PREFIX + secrets.token_urlsafe(24)
    return raw, raw[:PREFIX_LEN], hash_key(raw)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_key(raw: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(raw), key_hash)
