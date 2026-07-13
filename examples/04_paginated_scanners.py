"""04: Cursor-free browsing with the paginated scanners.

Every list endpoint on the Batch API is cursor-paginated (`limit` +
`next_cursor`). Threading cursors by hand is the #1 source of bugs
in user code, so the SDK ships scanners that drain to exhaustion:

  - `client.iter_jobs(...)`     → `Iterator[JobHandle]`
  - `client.iter_runs(job_id)`  → `Iterator[RunHandle]`
  - `client.iter_results(job_id, ...)` → `Iterator[TaskResult]`

Each is a generator — memory stays bounded; pages are fetched on
demand. Filtering kwargs (`job_type=`, `status=`) pass through to
the underlying list call.

You still have `list_jobs / list_runs / list_results` if you want
the raw page + cursor.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/04_paginated_scanners.py
    python examples/04_paginated_scanners.py --job-id 01J…
"""

import argparse
import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import JobStatus, JobType


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--job-id",
        default=None,
        help="Optional job id; if set, also lists every run of that job.",
    )
    args = parser.parse_args()

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    # Show every closed regular job, newest first. Each yielded item
    # is a JobHandle with `.data` already populated from the page.
    print("--- closed regular jobs ---")
    for job in client.iter_jobs(job_type=JobType.REGULAR, status=JobStatus.CLOSED):
        latest = job.data.latest_run
        total = latest.stats.total if latest else 0
        print(f"{job.job_id}  {job.status.value:<8}  tasks={total}")

    # For one job, walk all its runs (handy for `/rerun`-heavy
    # workflows). `client.iter_runs` yields RunHandle; `.data` is
    # pre-populated from the page.
    if args.job_id:
        print(f"\n--- runs of {args.job_id} ---")
        for run in client.iter_runs(args.job_id, page_size=50):
            r = run.data
            print(f"  {run.run_id}  #{r.run_sequence}  {r.status.value}")


if __name__ == "__main__":
    main()
