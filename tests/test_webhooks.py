"""verify_webhook: signatures built the way the Batch API signs them."""

import base64
import hashlib
import hmac
import os

from zenrows.batch import verify_webhook

RAW = os.urandom(32)
SECRET_B64 = base64.b64encode(RAW).decode()
BODY = b'{"schema":1,"event_type":"run.completed","job_id":"j1","run_id":"r1"}'
T = 1_760_000_000


def sign(body: bytes = BODY, t: int = T, key: bytes = RAW, kid: str = "k1") -> str:
    digest = hmac.new(key, f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest},kid={kid}"


def test_accepts_a_genuine_delivery():
    assert verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T)


def test_accepts_str_body_and_spaces_in_header():
    header = sign().replace(",", ", ")
    assert verify_webhook(BODY.decode(), header, {"k1": SECRET_B64}, now=T)


def test_rejects_a_tampered_body():
    assert not verify_webhook(BODY + b" ", sign(), {"k1": SECRET_B64}, now=T)


def test_rejects_the_undecoded_secret_used_as_key():
    # The mistake this helper exists to prevent: HMAC over the base64 text.
    header = sign(key=SECRET_B64.encode())
    assert not verify_webhook(BODY, header, {"k1": SECRET_B64}, now=T)


def test_picks_the_secret_by_kid_during_rotation():
    old = base64.b64encode(os.urandom(32)).decode()
    secrets = {"k0": old, "k1": SECRET_B64}
    assert verify_webhook(BODY, sign(kid="k1"), secrets, now=T)
    assert not verify_webhook(BODY, sign(kid="k0"), secrets, now=T)


def test_rejects_unknown_kid_and_malformed_input():
    assert not verify_webhook(BODY, sign(kid="nope"), {"k1": SECRET_B64}, now=T)
    assert not verify_webhook(BODY, "garbage", {"k1": SECRET_B64}, now=T)
    assert not verify_webhook(BODY, "", {"k1": SECRET_B64}, now=T)
    assert not verify_webhook(BODY, sign(), {"k1": "not base64!"}, now=T)
    assert not verify_webhook(BODY, sign().replace(f"t={T}", "t=abc"), {"k1": SECRET_B64}, now=T)


def test_timestamp_tolerance():
    assert verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T + 300)
    assert not verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T + 301)
    assert not verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T - 301)
    assert verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T + 10_000, tolerance_seconds=0)


def test_malformed_headers_return_false_instead_of_raising():
    """Bad input from the network must be rejected, never crash the receiver."""
    s = {"k1": SECRET_B64}
    good = sign().split(",")[1]  # v1=<hex>
    cases = [
        "t=1,v1=é,kid=k1",  # non-ASCII digest
        f"t={chr(0x661)},{good},kid=k1",  # Arabic-Indic digit: int() would accept it
        f"t={'9' * 400},{good},kid=k1",  # huge timestamp
        f"t=-5,{good},kid=k1",
        f"t={T},v1=xyz,kid=k1",  # not hex
        f"t={T},v1={'a' * 63},kid=k1",  # wrong length
        f"t={T},{good}",  # no kid
        f"t=,{good},kid=k1",
    ]
    for header in cases:
        assert verify_webhook(BODY, header, s, now=1.5) is False, header
        assert verify_webhook(BODY, header, s, now=float(T)) is False, header


def test_unusual_now_values_do_not_raise():
    s = {"k1": SECRET_B64}
    assert verify_webhook(BODY, sign(), s, now=float("inf")) is False
    assert verify_webhook(BODY, sign(), s, now=float("nan")) is False
    assert verify_webhook(BODY, sign(), s, now=1e308) is False


def test_uppercase_hex_digest_is_accepted():
    t, v1, kid = sign().split(",")
    header = ",".join([t, v1.upper().replace("V1=", "v1="), kid])
    assert verify_webhook(BODY, header, {"k1": SECRET_B64}, now=T)


def test_negative_or_bad_tolerance_rejects():
    """Only 0 disables the age check; a negative value must not."""
    s = {"k1": SECRET_B64}
    assert verify_webhook(BODY, sign(), s, now=T, tolerance_seconds=-1) is False
    assert verify_webhook(BODY, sign(), s, now=T + 10_000, tolerance_seconds=-1) is False
    assert verify_webhook(BODY, sign(), s, now=T, tolerance_seconds=float("nan")) is False
    assert verify_webhook(BODY, sign(), s, now=T + 10, tolerance_seconds=30.5)


def test_wrong_typed_arguments_return_false():
    s = {"k1": SECRET_B64}
    header = sign()
    assert verify_webhook(BODY, header.encode(), s, now=T) is False  # type: ignore[arg-type]
    assert verify_webhook(BODY, 123, s, now=T) is False  # type: ignore[arg-type]
    assert verify_webhook(BODY, None, s, now=T) is False  # type: ignore[arg-type]
    assert verify_webhook(BODY, header, None, now=T) is False  # type: ignore[arg-type]
    assert verify_webhook(BODY, header, {"k1": 42}, now=T) is False  # type: ignore[dict-item]
    assert verify_webhook(None, header, s, now=T) is False  # type: ignore[arg-type]
    assert verify_webhook(BODY, header, s, now="soon") is False  # type: ignore[arg-type]
    assert verify_webhook(bytearray(BODY), header, s, now=T)


def test_huge_tolerance_returns_false_instead_of_raising():
    assert (
        verify_webhook(BODY, sign(), {"k1": SECRET_B64}, now=T, tolerance_seconds=10**1000) is False
    )


def test_bad_now_is_rejected_even_when_the_age_check_is_off():
    s = {"k1": SECRET_B64}
    for bad in (float("nan"), float("inf"), float("-inf"), 10**1000, True, "soon"):
        assert verify_webhook(BODY, sign(), s, now=bad, tolerance_seconds=0) is False, bad
    assert verify_webhook(BODY, sign(), s, now=T, tolerance_seconds=0)


def test_empty_or_wrong_length_secret_never_verifies():
    """With an empty key anyone can compute a valid HMAC: refuse it."""
    forged = sign(key=b"")
    assert verify_webhook(BODY, forged, {"k1": ""}, now=T) is False
    short = base64.b64encode(os.urandom(16)).decode()
    assert verify_webhook(BODY, sign(key=base64.b64decode(short)), {"k1": short}, now=T) is False
    long = base64.b64encode(os.urandom(64)).decode()
    assert verify_webhook(BODY, sign(key=base64.b64decode(long)), {"k1": long}, now=T) is False


def test_unreadable_secrets_or_body_return_false():
    class Exploding(dict):
        def get(self, key, default=None):
            raise RuntimeError("store unavailable")

    assert verify_webhook(BODY, sign(), Exploding(), now=T) is False
    # A str body with a lone surrogate can't be UTF-8 encoded.
    assert verify_webhook("\ud800", sign(), {"k1": SECRET_B64}, now=T) is False
    assert verify_webhook(memoryview(BODY), sign(), {"k1": SECRET_B64}, now=T)
