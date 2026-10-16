"""`verify_webhook_signature` against signatures computed the way the
Batch API signs deliveries:
`t=<unix>,v1=<hex HMAC-SHA256(key, "<t>.<raw body>")>,kid=<kid>`, keyed
with the 32 bytes the base64 secret decodes to."""

import base64
import hashlib
import hmac

import pytest

from zenrows.batch import (
    DEFAULT_WEBHOOK_TOLERANCE,
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    WebhookEvent,
    WebhookEventType,
    verify_webhook_signature,
)

# A fixed vector produced by the API's own signing routine, with the
# secret bytes 0x00..0x1f, kid "kid_live" and t=1790000000.
SECRET = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
BODY = (
    b'{"schema":"v1","event_id":"01JEVENT0000000000000000000","event_type":"run.completed",'
    b'"occurred_at":"2026-10-16T12:00:00Z","job_id":"01JJOB00000000000000000000",'
    b'"run_id":"01JRUN00000000000000000000","run_sequence":1}'
)
T = 1790000000
HEADER = (
    "t=1790000000,v1=4f5ee4f729590ac8b2f37163ecd9f88bf3fb500632fe2a3f509cced90d177c5b,kid=kid_live"
)


def _sign(secret_b64: str, body: bytes, t: int, kid: str) -> str:
    key = base64.b64decode(secret_b64)
    digest = hmac.new(key, f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest},kid={kid}"


def test_fixture_vector_verifies():
    assert verify_webhook_signature(BODY, HEADER, {"kid_live": SECRET}, now=T)
    assert verify_webhook_signature(BODY.decode(), HEADER, {"kid_live": SECRET}, now=T + 299)


def test_fixture_matches_local_signer():
    """The vector is plain HMAC over `<t>.<body>` with the decoded key."""
    assert _sign(SECRET, BODY, T, "kid_live") == HEADER


def test_the_secret_is_base64_decoded_not_used_as_text():
    """Signing with the base64 text itself (a common mistake) never verifies."""
    digest = hmac.new(SECRET.encode(), f"{T}.".encode() + BODY, hashlib.sha256).hexdigest()
    header = f"t={T},v1={digest},kid=kid_live"
    assert not verify_webhook_signature(BODY, header, {"kid_live": SECRET}, now=T)


def test_tampered_body_or_digest_fails():
    secrets = {"kid_live": SECRET}
    assert not verify_webhook_signature(BODY + b" ", HEADER, secrets, now=T)
    flipped = HEADER.replace("v1=4f", "v1=5f")
    assert not verify_webhook_signature(BODY, flipped, secrets, now=T)


def test_kid_selects_the_secret_during_rotation():
    new_secret = base64.b64encode(bytes(range(32, 64))).decode()
    secrets = {"kid_old": SECRET, "kid_new": new_secret}
    assert verify_webhook_signature(BODY, _sign(SECRET, BODY, T, "kid_old"), secrets, now=T)
    assert verify_webhook_signature(BODY, _sign(new_secret, BODY, T, "kid_new"), secrets, now=T)
    # Signed with the old key but labelled with the new kid: rejected.
    assert not verify_webhook_signature(BODY, _sign(SECRET, BODY, T, "kid_new"), secrets, now=T)
    # Unknown kid: rejected.
    assert not verify_webhook_signature(BODY, HEADER, {"other": SECRET}, now=T)


def test_tolerance():
    secrets = {"kid_live": SECRET}
    assert DEFAULT_WEBHOOK_TOLERANCE == 300
    assert verify_webhook_signature(BODY, HEADER, secrets, now=T + 300)
    assert verify_webhook_signature(BODY, HEADER, secrets, now=T - 300)
    assert not verify_webhook_signature(BODY, HEADER, secrets, now=T + 301)
    assert not verify_webhook_signature(BODY, HEADER, secrets, now=T - 301)
    assert verify_webhook_signature(BODY, HEADER, secrets, tolerance=60, now=T + 60)
    assert not verify_webhook_signature(BODY, HEADER, secrets, tolerance=60, now=T + 61)
    # 0 turns the check off; a negative tolerance rejects everything.
    assert verify_webhook_signature(BODY, HEADER, secrets, tolerance=0, now=T + 10**6)
    assert not verify_webhook_signature(BODY, HEADER, secrets, tolerance=-1, now=T)
    # Default `now` is the real clock, far past T.
    assert not verify_webhook_signature(BODY, HEADER, secrets)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "garbage",
        "t=1790000000,kid=kid_live",
        "v1=4f5ee4f729590ac8b2f37163ecd9f88bf3fb500632fe2a3f509cced90d177c5b,kid=kid_live",
        "t=1790000000,v1=4f5ee4f729590ac8b2f37163ecd9f88bf3fb500632fe2a3f509cced90d177c5b",
        "t=abc,v1=4f5ee4f729590ac8b2f37163ecd9f88bf3fb500632fe2a3f509cced90d177c5b,kid=kid_live",
        "t=1790000000,v1=zz,kid=kid_live",
        "t=1790000000,v1=4f5e,kid=kid_live",
    ],
)
def test_malformed_headers_fail(header: str):
    assert not verify_webhook_signature(BODY, header, {"kid_live": SECRET}, now=T)


@pytest.mark.parametrize(
    "secret",
    ["", "not base64!!", base64.b64encode(b"short").decode(), SECRET.rstrip("=")],
)
def test_bad_secrets_never_verify(secret: str):
    """An empty, non-base64 or wrong-length secret never verifies, even
    against a header signed with that same key."""
    header = f"t={T},v1={hmac.new(b'', f'{T}.'.encode() + BODY, hashlib.sha256).hexdigest()},kid=k"
    assert not verify_webhook_signature(BODY, header, {"k": secret}, now=T)


def test_wrong_argument_types_return_false():
    assert not verify_webhook_signature(BODY, None, {"kid_live": SECRET}, now=T)  # type: ignore[arg-type]
    assert not verify_webhook_signature(BODY, HEADER, None, now=T)  # type: ignore[arg-type]
    assert not verify_webhook_signature(123, HEADER, {"kid_live": SECRET}, now=T)  # type: ignore[arg-type]
    assert not verify_webhook_signature(BODY, HEADER, {"kid_live": SECRET}, now=float("nan"))


def test_header_names_and_payload_model():
    assert SIGNATURE_HEADER == "X-Signature"
    assert EVENT_ID_HEADER == "X-ZenRows-Event-Id"
    event = WebhookEvent.model_validate_json(BODY)
    assert event.event_type is WebhookEventType.RUN_COMPLETED
    assert event.run_sequence == 1
