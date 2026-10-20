"""Verify the signature on a Batch webhook delivery.

When a job's webhook has `signature: true`, every delivery carries an
`X-Signature` header shaped `t=<unix seconds>,v1=<hex digest>,kid=<key id>`.
The digest is HMAC-SHA256 over the bytes `<t>.<raw request body>`, keyed
with your signing secret. The API returns that secret once, base64-encoded,
from `rotate_hmac_key()`; `verify_webhook_signature` decodes it for you.

    from zenrows.batch import WEBHOOK_SIGNATURE_HEADER, verify_webhook_signature

    ok = verify_webhook_signature(
        request.body,                       # raw bytes, before any JSON parsing
        request.headers[WEBHOOK_SIGNATURE_HEADER],
        {"<kid>": "<base64 secret>"},       # every key you may still receive
    )

During a key rotation, keep both the old and the new secret in the
mapping: the header's `kid` picks the one that signed. Deliveries can
repeat, so deduplicate on the `X-ZenRows-Event-Id` header
(`WEBHOOK_EVENT_ID_HEADER`).
"""

import base64
import binascii
import hashlib
import hmac
import math
import re
import time
from collections.abc import Mapping

WEBHOOK_SIGNATURE_HEADER = "X-Signature"
"""The header that carries a signed delivery's signature."""

WEBHOOK_EVENT_ID_HEADER = "X-ZenRows-Event-Id"
"""The header that carries a delivery's event id. Deduplicate on it."""

DEFAULT_WEBHOOK_TOLERANCE_SECONDS = 300
"""Seconds a signature's timestamp may be from the current time."""

# The API issues signing secrets of exactly 32 random bytes. Refusing any
# other length also refuses an empty key, which anyone could sign with.
_SECRET_BYTES = 32

# A Unix timestamp in seconds, bounded so the arithmetic can't overflow,
# and a hex HMAC-SHA256 digest (64 characters).
_TIMESTAMP = re.compile(r"[0-9]{1,12}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)


def verify_webhook_signature(
    raw_body: bytes | str,
    signature_header: str,
    secrets: Mapping[str, str],
    *,
    tolerance_seconds: float = DEFAULT_WEBHOOK_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Return True when `signature_header` is a valid signature of `raw_body`.

    - `raw_body`: the request body exactly as received (a `str` is
      encoded as utf-8). Verify before parsing the JSON.
    - `signature_header`: the value of the `X-Signature` header.
    - `secrets`: maps each key id (`kid`) to its secret as the API
      returned it: a base64 string that decodes to 32 bytes. Any other
      secret, including an empty one, never verifies.
    - `tolerance_seconds`: the largest accepted gap, in seconds, between the
      signature's timestamp and `now`, in either direction. `0` turns
      the check off; a negative value rejects every delivery.
    - `now`: the current Unix time in seconds (default: `time.time()`).

    The digests are compared in constant time. Returns False, and never
    raises, for a malformed header, an unknown `kid`, a bad secret, a
    wrong digest or a timestamp out of range.
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
    if not t or not digest or not kid:
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
    """A real int or float (not a bool) that is finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


__all__ = [
    "DEFAULT_WEBHOOK_TOLERANCE_SECONDS",
    "WEBHOOK_EVENT_ID_HEADER",
    "WEBHOOK_SIGNATURE_HEADER",
    "verify_webhook_signature",
]
