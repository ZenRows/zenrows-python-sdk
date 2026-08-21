"""09: Scheduled jobs — the full lifecycle.

A `scheduled` job runs its task template automatically on a cadence;
each fire produces a fresh Run. This walks the actions you'll actually
use:

  - Create with the three typed cadence builders: `Rate` (fixed
    interval), `Calendar` (times-of-day on a Daily/Weekly/Monthly
    cadence), and `At` (a one-shot future fire).
  - Attach a webhook so each fire's outcome is pushed to you — the
    natural signal for a fire-and-forget job (`run.completed`, or
    `run.failed` with a `failure_reason` on an account-level error).
  - Manage the schedule via the `job.schedule` facet:
    `pause()` / `resume()` the fires and `update()` the cadence.
    (Distinct from `job.run.pause()`, which suspends the current run.)
  - Inspect the Runs each fire produced and re-run a fire's failures.
  - Tear it down with `delete()`.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/09_scheduled_jobs.py
"""

import os
from datetime import datetime

from zenrows import ZenRowsBatchClient
from zenrows.batch import At, Calendar, JobHandle, Monthly, Rate, Weekly


def _schedule_state(job: JobHandle) -> str:
    """schedule_state is None on non-scheduled / legacy rows; here it's
    always set, but keep the read total for the type-checker."""
    state = job.data.schedule_state
    return state.value if state else "?"


def main() -> None:
    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    # --- 1. Create: a recurring job on a fixed interval -----------------
    # `Rate` fires every N minutes/hours/days from creation. Attach a
    # webhook: scheduled jobs are fire-and-forget, so a webhook is how you
    # learn each fire finished (`run.completed`) or auto-failed on an
    # account error (`run.failed`, carrying `failure_reason`).
    job = client.submit_scheduled(
        Rate(every=6, unit="hour"),
        [
            {"url": "https://example.com/prices", "external_id": "prices"},
            {"url": "https://example.com/stock", "external_id": "stock"},
        ],
        zenrows_params={"js_render": "true"},
        name="market-poller",
        webhook={"url": "https://hooks.example.com/zenrows", "signature": True},
    )
    print(f"created scheduled job {job.job_id} — every 6h ({job.status.value})")

    # --- 2. Other cadences ----------------------------------------------
    # Calendar: specific wall-clock times on a Weekly/Daily/Monthly
    # cadence, in an explicit timezone.
    weekly = client.submit_scheduled(
        Calendar(
            times_of_day=["09:00", "18:00"],
            cadence=Weekly(days=["mon", "wed", "fri"]),
            timezone="Europe/Berlin",
        ),
        ["https://example.com/report"],
        name="triweekly-report",
    )
    print(f"created {weekly.job_id} — Mon/Wed/Fri 09:00+18:00 Berlin")

    # Monthly on given day-of-month numbers.
    monthly = client.submit_scheduled(
        Calendar(
            times_of_day=["00:00"],
            cadence=Monthly(days=[1, 15]),
            timezone="UTC",
        ),
        ["https://example.com/invoice"],
        name="billing-scrape",
    )
    print(f"created {monthly.job_id} — 1st + 15th at midnight UTC")

    # At: a single future fire (naive local datetime + timezone).
    oneshot = client.submit_scheduled(
        At(datetime(2026, 9, 1, 9, 0), timezone="Europe/Berlin"),
        ["https://example.com/launch-day"],
        name="launch-day",
    )
    print(f"created {oneshot.job_id} — one-shot 2026-09-01 09:00 Berlin")

    # --- 3. Manage the schedule (via the `job.schedule` facet) ----------
    # Pause: the schedule keeps ticking server-side but fires are dropped
    # until resume(). Idempotent. Each op returns a fresh JobHandle with
    # the updated state.
    paused = job.schedule.pause()
    print(f"paused {job.job_id} (schedule_state={_schedule_state(paused)})")

    # Change the cadence while paused. An in-flight run keeps running; the
    # new schedule governs only future fires.
    job.schedule.update(Rate(every=30, unit="minute"))
    print(f"re-scheduled {job.job_id} → every 30m")

    # Resume fires.
    resumed = job.schedule.resume()
    print(f"resumed {job.job_id} (schedule_state={_schedule_state(resumed)})")

    # --- 4. Fire it now and inspect the resulting run -------------------
    # A scheduled job's fires ARE runs. Rather than wait for the cadence,
    # `rerun()` on a scheduled job with no prior run fires it from the
    # template immediately — handy to smoke-test the job right after
    # creating it. (`job.run.load()` would raise here otherwise, since
    # a fresh schedule hasn't fired yet.)
    run = job.rerun()
    print(f"manually fired {job.job_id} → run {run.run_id}")

    run.wait(timeout=600.0)  # block until this fire is terminal
    s = run.data.stats
    print(f"  {run.data.status.value}: {s.successful}/{s.total} ok")
    # A fire that auto-failed on an account error exposes why.
    if run.data.status.value == "failed":
        print(f"  auto-failed: {run.data.failure_reason}")
    # Re-run just this fire's failures — successful tasks are inherited, so
    # you only pay to re-scrape what failed.
    if s.failed:
        retry = job.retry_failed()
        print(f"  retried failures → run {retry.run_id}")

    # Every fire (scheduled or manual) is a Run under the job, newest-first.
    for r in job.runs():
        print(f"  run {r.run_id} #{r.data.run_sequence} {r.data.status.value}")

    # --- 5. Tear down ---------------------------------------------------
    for handle in (job, weekly, monthly, oneshot):
        handle.delete()
    print("deleted all demo jobs")


if __name__ == "__main__":
    main()
