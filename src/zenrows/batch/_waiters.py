"""Polling helpers used by `ZenRowsBatchClient.wait_for_*` methods.

Convention: a *waiter* polls a resource until it reaches a target
state or a timeout/error fires. We default to exponential-ish
backoff (capped) so short jobs don't pay a multi-second poll
cadence and long ones don't hammer the API every two seconds.

Errors are `WaiterTimeout` (timed out) and `WaiterError` (predicate
raised, or the resource transitioned into an unexpected state the
caller flagged as failure). Both subclass the stdlib `TimeoutError`
/ `RuntimeError` so callers can catch with the broad stdlib types
too.
"""

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class WaiterTimeout(TimeoutError):
    """Raised when a waiter's `timeout` elapsed before the target state."""


class WaiterError(RuntimeError):
    """Raised when the resource entered a `failure_states` value."""


def poll_until(
    fetch: Callable[[], T],
    *,
    is_done: Callable[[T], bool],
    is_failure: Callable[[T], bool] | None = None,
    timeout: float = 300.0,
    initial_interval: float = 1.0,
    max_interval: float = 15.0,
    backoff: float = 1.5,
    jitter: float = 0.2,
) -> T:
    """Generic poll loop.

    Calls `fetch()` repeatedly until `is_done(value)` is true (returns
    the value) or `is_failure(value)` is true (raises `WaiterError`),
    or `timeout` seconds elapse (raises `WaiterTimeout`).

    The wait between calls starts at `initial_interval`, multiplies by
    `backoff` each iteration, caps at `max_interval`, and is jittered
    by ±`jitter` fraction so concurrent waiters don't synchronise into
    thundering-herd patterns against the API.
    """
    deadline = time.monotonic() + timeout
    interval = initial_interval
    while True:
        value = fetch()
        if is_done(value):
            return value
        if is_failure and is_failure(value):
            raise WaiterError(f"waiter: resource entered failure state ({value!r})")
        now = time.monotonic()
        if now >= deadline:
            raise WaiterTimeout(f"waiter: timed out after {timeout:.1f}s waiting for target state")
        # Jittered sleep, but never overshoot the deadline.
        sleep = min(interval, deadline - now)
        sleep = sleep * (1.0 + random.uniform(-jitter, jitter))
        if sleep > 0:
            time.sleep(sleep)
        interval = min(interval * backoff, max_interval)
