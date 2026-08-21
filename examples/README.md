# Examples

Runnable samples for the Batch API client (`ZenRowsBatchClient`).
Each script reads the API key from the environment:

```bash
export ZENROWS_API_KEY=zr_...
```

Script-specific flags (`--job-id`, `--out`, …) are surfaced via
`argparse`; each file's `--help` lists them.

```bash
uv run python examples/01_submit_and_wait.py
uv run python examples/02_download_to_dir.py --job-id 01J...
```

| Sample | Highlights |
|---|---|
| `01_submit_and_wait.py`        | `submit_regular` → `job.wait()` → `run.results()` |
| `02_download_to_dir.py`        | bulk download to disk with `concurrency=` + `progress=` |
| `03_csv_input.py`              | `upload_csv` helper end-to-end (slot + PUT + submit) |
| `04_paginated_scanners.py`     | `iter_jobs` + `iter_runs` for cursor-free browsing |
| `05_error_handling.py`         | RFC 7807 → `BatchAPIError.code` branching |
| `06_retry_failed.py`           | `retry_failed()` — partial rerun of only the failed tasks |
| `07_hmac_rotation.py`          | rotate / finalize / cancel lifecycle |
| `08_download_all_results.py`   | `download_all_results()` — server-side export zip of a whole run |
| `09_scheduled_jobs.py`         | scheduled cadences (`Rate`/`Calendar`/`At`), `pause`/`resume`/`update_schedule`, per-fire runs, `retry_failed` |
| `10_lightweight_handles.py`    | `client.job(id)` / `client.run(id, rid)` — act on a known id without a GET (`.data` lazily fetches) |

The samples are deliberately small — each demonstrates one feature
end-to-end so they double as living documentation.
