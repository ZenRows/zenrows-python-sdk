"""06: Retry only the failed tasks of a finished run.

`job.retry_failed()` starts a fresh run that re-executes ONLY the
previous run's failed tasks (partial retry, SPEC §3.5). Successful
tasks are inherited verbatim — you don't pay to re-scrape them, and
the new run's totals already carry the prior successes. Pass
`include_pending=True` to also re-enqueue tasks that never started
(handy after a `stop()`). It's a thin shortcut for
`job.rerun(status="failed")`.

Requires the previous run to be terminal (`completed` / `stopped`);
otherwise the API returns `409 run_not_terminal` — call
`job.run.stop()` first if it's still live.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/06_retry_failed.py --job-id 01J…
"""

import argparse
import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-id", required=True, help="Job whose latest run finished.")
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="Also retry tasks that never started (status=failed,pending).",
    )
    args = parser.parse_args()

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])
    job = client.get_job(args.job_id)

    before = job.data.latest_run
    if before:
        t = before.stats
        print(f"latest run {before.run_id}: {t.failed} failed / {t.total} total")

    try:
        run = job.retry_failed(include_pending=args.include_pending)
    except BatchAPIError as e:
        # e.g. no_matching_tasks (nothing failed) or run_not_terminal.
        print(f"retry skipped: {e.code}")
        return

    print(f"retry run {run.run_id} started; waiting…")
    run = run.wait(timeout=600.0)
    t = run.data.stats
    print(f"run {run.run_id} {run.data.status.value}: {t.successful}/{t.total} successful")


if __name__ == "__main__":
    main()
