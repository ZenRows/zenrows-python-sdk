"""07: Per-org HMAC key lifecycle.

The Batch API holds an HMAC keyset per org for signing future
outbound webhooks. Two slots — `active` (used for signing) and
`candidate` (staged next-active during rotation). The state machine:

    (empty) ──rotate──► [active=K1]
    [K1]   ──rotate──► [active=K1, candidate=K2]
    [K1,K2] ─finalize→ [active=K2]        (K1 discarded)
    [K1,K2] ─cancel──► [active=K1]        (K2 discarded)

The `secret` field is returned **only** in the `/rotate` response —
this is your one chance to capture it. Subsequent reads give you
just `kid` + `created_at`.

Typical client flow:
  1. `rotate_hmac_key()`        → install K2 alongside K1 in your verifier
  2. (deploy K2 everywhere)
  3. `finalize_hmac_key()`      → server starts signing with K2; drop K1

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/07_hmac_rotation.py
"""

import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError


def main() -> None:
    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    keys = client.list_hmac_keys()
    print(f"active:    {keys.active and keys.active.kid}")
    print(f"candidate: {keys.candidate and keys.candidate.kid}")

    # Initial generation OR staging a candidate — same endpoint, the
    # server decides which based on current state.
    try:
        rotated = client.rotate_hmac_key()
        # Capture `rotated.secret` HERE — it is not returned again.
        print(f"new key kid={rotated.kid} secret={rotated.secret!r}")
    except BatchAPIError as exc:
        if exc.code == "candidate_pending":
            print("a candidate is already staged — finalize or cancel first")
            return
        raise

    # Once your verifiers accept the new key, promote it:
    # client.finalize_hmac_key()
    # ...or back out:
    # client.cancel_hmac_rotation()


if __name__ == "__main__":
    main()
