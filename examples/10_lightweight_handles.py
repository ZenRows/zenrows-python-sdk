"""10: Lightweight refs — act on a job/run id without a GET.

`client.job(job_id)` and `client.run(job_id, run_id)` mint a `JobRef` /
`RunRef` with **no network call**. Lifecycle operations (`delete`, `stop`,
`close`, `rerun`, `retry_failed`, `add_tasks`) act on the id directly — so
when an id arrives from a webhook, a queue, or your own DB, you skip the
round-trip that `get_job` would spend just to fetch data you don't need.

A ref carries no `.data`; call `.load()` for a `JobHandle` / `RunHandle`
whose `.data` snapshot is ready. Reach for `get_job` / `get_run` when you
want that data eagerly up front (they're `client.job(id).load()` in one
call).

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/10_lightweight_handles.py --job-id 01J... [--run-id 01J...] [--delete]
"""

import argparse
import os

from zenrows import ZenRowsBatchClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--run-id", default=None, help="scrub just this run (with --delete)")
    ap.add_argument("--delete", action="store_true", help="actually stop + delete (destructive)")
    args = ap.parse_args()

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    # Minting a ref is free — no request goes out on this line.
    job = client.job(args.job_id)

    # `.load()` does the one GET, returning a JobHandle with `.data`.
    print(f"{args.job_id}: status={job.load().data.status.value}")

    if args.delete:
        # Act on the id directly — each of these is a single request with no
        # preceding GET (contrast `get_job(id).delete()`, which fetches first).
        if args.run_id:
            client.run(args.job_id, args.run_id).delete()  # DELETE one run only
            print(f"deleted run {args.run_id}")
        client.job(args.job_id).run.stop()  # POST /jobs/{id}/stop (current run)
        client.job(args.job_id).delete()  # DELETE /jobs/{id}
        print(f"stopped + deleted {args.job_id}")
    else:
        print("(re-run with --delete to stop + delete via GET-free refs)")


if __name__ == "__main__":
    main()
