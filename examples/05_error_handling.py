"""05: RFC 7807 → `BatchAPIError` branching.

Every non-2xx response from the Batch API is a Problem JSON body
with a stable `code` field. The SDK parses it once and exposes:

  - `BatchAPIError.code`       — short string (`not_found`,
                                  `idempotency_key_conflict`,
                                  `file_input_not_found`, ...). Safe
                                  to switch on.
  - `BatchAPIError.status_code`— int from the HTTP response.
  - `BatchAPIError.problem`    — full `ProblemDetail` if the body
                                  parsed; `None` otherwise.

Switching on `code` is the recommended pattern — status codes are
not 1:1 with semantics (multiple 409s, multiple 404s, etc.).

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/05_error_handling.py
"""

import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError


def main() -> None:
    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    try:
        client.get_job("definitely-not-a-real-job-id")
    except BatchAPIError as exc:
        match exc.code:
            case "not_found":
                print(f"job missing or foreign: {exc.problem and exc.problem.detail}")
            case "unauthenticated":
                print("bad API key — check ZENROWS_API_KEY")
            case "payment_required":
                print("out of credits — top up at zenrows.com")
            case _:
                # Unknown code: log + re-raise so it surfaces to a
                # monitoring layer instead of being silently swallowed.
                print(f"unexpected {exc.status_code} ({exc.code}): {exc}")
                raise


if __name__ == "__main__":
    main()
