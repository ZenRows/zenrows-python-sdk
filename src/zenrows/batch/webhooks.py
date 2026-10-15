"""Verify the signature on an incoming Batch webhook delivery.

A signed delivery carries an `X-Signature` header shaped
`t=<unix seconds>,v1=<hex digest>,kid=<key id>`. The digest is
HMAC-SHA256 over the bytes `<t>.<raw request body>`, keyed with the
signing secret. The API shows that secret once, base64-encoded, when you
create or rotate a key; this helper decodes it for you.

    from zenrows.batch import verify_webhook

    ok = verify_webhook(
        request.body,                        # raw bytes, before any JSON parsing
        request.headers["X-Signature"],
        {"kid_123": "q3Jk...base64...="},    # every key you may still receive
    )

Keep the previous key in `secrets` while a rotation is in progress:
deliveries name the key they were signed with in `kid`.
"""

import base64
import binascii
import hashlib
import hmac
import math
import re
import time
from collections.abc import Mapping

DEFAULT_TOLERANCE_SECONDS = 300

# The API issues signing secrets of exactly 32 random bytes. Refusing any
# other length also refuses an empty key, which anyone could sign with.
_SECRET_BYTES = 32

# A Unix timestamp in seconds (ASCII digits, bounded so arithmetic can't
# overflow) and a SHA-256 hex digest. Anything else is malformed.
_TIMESTAMP = re.compile(r"[0-9]{1,12}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)


def verify_webhook(
    raw_body: bytes | str,
    signature_header: str,
    secrets: Mapping[str, str],
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Return True when `signature_header` is a valid signature of `raw_body`.

    `raw_body` must be the exact bytes received (a `str` is encoded as
    UTF-8). `secrets` maps key id (`kid`) to the base64 secret the API
    returned; a secret that doesn't decode to 32 bytes (including an
    empty one) never verifies. The header's `kid` picks the secret; an unknown `kid`, a
    malformed header or secret, a wrong digest, or a timestamp more than
    `tolerance_seconds` away from `now` (default: the current time) all
    return False. Pass `tolerance_seconds=0` to skip the timestamp check;
    a negative tolerance rejects every delivery. It never raises on bad
    input, including arguments of the wrong type: a delivery it can't
    read is rejected.
    """
    if not isinstance(signature_header, str) or not isinstance(secrets, Mapping):
        return False
    if not isinstance(raw_body, (bytes, bytearray, memoryview, str)):
        return False
    if not _finite_number(tolerance_seconds) or tolerance_seconds < 0:
        return False
    if now is not None and not _finite_number(now):
        return False
    parts: dict[str, str] = {}
    for item in signature_header.split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts[key] = value
    t, digest, kid = parts.get("t"), parts.get("v1"), parts.get("kid")
    if t is None or digest is None or kid is None:
        return False
    if not _TIMESTAMP.fullmatch(t) or not _DIGEST.fullmatch(digest):
        return False
    try:
        secret_b64 = secrets.get(kid)
    except Exception:
        return False
    if not isinstance(secret_b64, str):
        return False
    try:
        key = base64.b64decode(secret_b64, validate=True)
    except (ValueError, binascii.Error):
        return False
    if len(key) != _SECRET_BYTES:
        return False
    if tolerance_seconds > 0:
        # `now` and the timestamp are both validated finite numbers here.
        current = time.time() if now is None else now
        if abs(current - int(t)) > tolerance_seconds:
            return False
    try:
        body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    except (TypeError, ValueError, UnicodeError):
        return False
    expected = hmac.new(key, t.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.lower())


def _finite_number(value: object) -> bool:
    """A real int or float (not a bool) that is finite. Ints too large
    for a float count as not finite rather than raising."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


__all__ = ["DEFAULT_TOLERANCE_SECONDS", "verify_webhook"]
