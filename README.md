<p align="center">
    <picture>
        <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo/dark.svg"/>
        <img alt="Zenrows Logo" src=".github/assets/logo/light.svg" width="300" />
    </picture>
</p>

# Zenrows Python SDK

SDK to access [Zenrows](https://www.zenrows.com/) APIs directly from Python.
Zenrows handles proxies rotation, headless browsers, and CAPTCHAs for you.

This package ships two clients:

  - **`ZenRowsClient`** — the original synchronous scraping client.
    One URL in, one HTML/JSON response out. Best for ad-hoc scraping.
  - **`ZenRowsBatchClient`** — the async-job / Batch API client. Submit
    thousands of URLs as one job, poll for results, optionally upload a
    CSV of URLs in one call. Best for offline / bulk pipelines.
    _(Private beta — [contact support](mailto:support@zenrows.com) for access.)_

## Installation

```bash
pip install zenrows
```

## Quickstart — synchronous scraping (`ZenRowsClient`)
Start using the API by [creating your API Key](https://www.zenrows.com/register?p=free).

The SDK uses [requests](https://docs.python-requests.org/) for HTTP requests. The client's response will be a requests `Response`.

It also uses [Retry](https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html) to automatically retry failed requests (status codes 429, 500, 502, 503, and 504). Retries are not active by default; you need to specify the number of retries, as shown below. It already includes an exponential back-off retry delay between failed requests.

`client.fetch()` is the primary method for the main page-scraping product; `client.get()` still works and is kept as a deprecated alias.

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY", retries=1)
url = "https://www.zenrows.com/"

response = client.fetch(url, params={
    # Our algorithm allows to automatically extract content from any website
    "autoparse": False,

    # CSS Selectors for data extraction (i.e. {"links":"a @href"} to get href attributes from links)
    "css_extractor": "",

    # Enable Javascript with a headless browser (5 credits)
    "js_render": False,

    # Use residential proxies (10 credits)
    "premium_proxy": False,

    # Make your request from a given country. Requires premium_proxy
    "proxy_country": "us",

    # Wait for a given CSS Selector to load in the DOM. Requires js_render
    "wait_for": ".content",

    # Wait a fixed amount of time in milliseconds. Requires js_render
    "wait": 2500,

    # Block specific resources from loading, check docs for the full list. Requires js_render
    "block_resources": "image,media,font",

    # Change the browser's window width and height. Requires js_render
    "window_width": 1920,
    "window_height": 1080,

    # Will automatically use either desktop or mobile user agents in the headers
    "device": "desktop",

    # Will return the status code returned by the website
    "original_status": False,
}, headers={
    "Referrer": "https://www.google.com",
    "User-Agent": "MyCustomUserAgent",
})

print(response.text)
```

You can also pass optional `params` and `headers`; the list above is a reference. For more info, check out [the documentation page](https://www.zenrows.com/documentation).

Sending headers to the target URL will overwrite our defaults. Be careful when doing it and contact us if there is any problem.

### Adaptive Stealth Mode

Set `mode="auto"` to let Zenrows pick the request configuration for you — it starts with the cheapest viable setup and escalates to `js_render`/`premium_proxy` only when the target needs it, billing only for the configuration that succeeds.

```python
response = client.get(url, params={"mode": "auto"})
```

Compatible with `proxy_country`, `js_instructions`, and `custom_headers`.

### POST Requests

The SDK also offers POST requests by calling the `client.post` method. It can receive a new parameter `data` that represents the data sent in, for example, a form. 

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY", retries=1)
url = "https://httpbin.org/anything"

response = client.post(url, data={
    "key1": "value1",
    "key2": "value2",
})

print(response.text)
```

### PUT Requests

The SDK also offers PUT requests by calling the `client.put` method. It can receive a new parameter `data` that represents the data sent in, for example, a form. 

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY", retries=1)
url = "https://httpbin.org/anything"

response = client.put(url, data={
    "key1": "value1",
    "key2": "value2",
})

print(response.text)
```

### Concurrency

To limit the concurrency, it uses [asyncio](https://docs.python.org/3/library/asyncio.html), which will simultaneously send a maximum of requests. The concurrency is determined by the plan you are in, so take a look at the [pricing](https://www.zenrows.com/pricing) and set it accordingly. Take into account that each client instance will have its own limit, meaning that two different scripts will not share it, and 429 (Too Many Requests) errors might arise.

The main difference with the sequential snippet above is `client.fetch_async` instead of `client.fetch`. The rest will work exactly the same. But the async is necessary to parallelize calls and allow async/await syntax. Remember to run the scripts with `asyncio.run` or it will fail with a `coroutine 'main' was never awaited` error.

We use `asyncio.gather` in the example below. It will wait for all the calls to finish, and the results are stored in a `responses` array. The whole list of URLs will run, even if some fail. Then each response will have the status, request, response content, and other values as usual.

```python
from zenrows import ZenRowsClient
import asyncio

client = ZenRowsClient("YOUR-API-KEY", concurrency=5, retries=1)

async def main():
    urls = [
        "https://www.zenrows.com/",
        # ...
    ]
    responses = await asyncio.gather(*[client.fetch_async(url) for url in urls])

    for response in responses:
        print(response.text)

asyncio.run(main())
```

### Extract

[Extract](https://docs.zenrows.com) (beta) runs a page through Zenrows' AI-powered structured extraction instead of returning raw HTML. Use `client.extract()` — it's the same request as `fetch()`, with the `extract` param set for you (defaults to `"auto"`; pass `"native"` or `"standard"` for the other contracts).

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY")
url = "https://www.zenrows.com/"

response = client.extract(url)  # extract: "auto"
# response = client.extract(url, mode="native")

print(response.json())
```

## Quickstart — Batch API (`ZenRowsBatchClient`)

> **⚠️ Beta.** The Batch API is currently in beta and not yet generally
> available — free to use while it's in beta, but the shape of the API may
> still change before GA.

For workflows where you have many URLs to scrape and don't want to
manage retries, concurrency, and pagination yourself, the Batch API
submits a *job* (a list of tasks), runs it asynchronously on Zenrows'
infrastructure, and lets you poll results when they're ready.

```python
from zenrows import ZenRowsBatchClient

client = ZenRowsBatchClient(api_key="YOUR-API-KEY")

# 1. Submit a job — URLs as bare strings or dicts with per-task fields.
#    submit_* returns a JobRef (no GET yet).
job = client.submit_regular(
    [
        {"url": "https://example.com/a", "external_id": "order-1"},
        {"url": "https://example.com/b", "external_id": "order-2"},
    ],
    zenrows_params={"js_render": "true", "premium_proxy": "true"},
)

# 2. Block until the current run is terminal; returns a RunHandle.
run = job.run.wait()
print(f"{run.stats.successful}/{run.stats.total} succeeded")

# 3. Inspect failures — each failed task carries an RFC 7807 error.
for task in run.results(status="failed"):
    code = task.error.code if task.error else "unknown"
    print(f"  {task.external_id or task.task_id} failed: {code}")

# 4. Download every successful body to ./out — one file per task.
count = run.download_to_dir("./out")
print(f"downloaded {count} results to ./out/")
```

> Need full control over the request body? `client.submit_job({...})` accepts a raw dict (the
> wire shape) or a typed `SubmitJobRequest`, and returns the same `JobRef`.

### Upload URLs from a CSV

```python
file_input_id = client.upload_csv(
    "leads.csv",
    fields={"url": "Page URL", "external_id": "Lead Ref"},
    header=True,
)
job = client.submit_regular(file_input_id=file_input_id)
```

`upload_csv` allocates the slot, PUTs your file to the presigned URL,
and returns the `file_input_id` — one call instead of three.

### Estimate cost before submitting

Pricing is per successful request (base `1`, `js_render` `5`,
`premium_proxy` `10`, both `25`; `mode=auto` is dynamic `1–25`).
`client.estimate_cost` answers "if every URL succeeds once, what's the
charge?" — pass the **same body you'd submit**, and get back a credit
interval (`min == max` unless the job uses `mode=auto`) with a per-tier
breakdown.

```python
est = client.estimate_cost(
    {
        "type": "regular",
        "tasks": [{"url": "https://a"}, {"url": "https://b"}],
        "zenrows_params": {"js_render": "true"},
    }
)
print(est)           # "10 credits (2 tasks)"
print(est.format())  # per-tier breakdown table
```

> Estimation is computed client-side from the SDK's rate card today, so
> it costs no API call; it may move server-side in a future release, so
> it lives on the client (`client.estimate_cost`) to keep your call
> sites stable.

After a run finishes, read the **actual** credits it used from
`run.stats.spend` (`{credits, cost}`). Only successful requests are
charged, so realized spend is ≤ the estimate. (Per-task `task.spend` is
indicative and often absent on successful rows — rely on the run-level
rollup for actuals.)

### Retry only the failed tasks

After a run finishes with some failures, `retry_failed()` starts a new
run that re-executes *only* the failed tasks — successes are inherited
verbatim, so you don't pay to re-scrape them. (Thin shortcut for
`rerun(status="failed")`.) Pass `include_pending=True` to also pick up
tasks that never started.

```python
# If you still hold the `job` ref from submit, act on it directly:
run = job.retry_failed()

# Or, when you only kept the id (a CLI arg, your DB, a webhook),
# `client.job(id)` acts on it with no GET:
run = client.job(job_id).retry_failed()

run.wait()
```

### Download results

The Batch API doesn't ship page bodies inline — `results()` gives you
metadata plus a presigned `result_url` per task. The SDK downloads bodies
straight from that URL (no extra API round-trip). Four ways, on the
current run (`job.run.*`) or any specific run
(`client.get_run(job_id, run_id=...)`):

| You want… | Use | Where it lands |
|---|---|---|
| Every body, one file per task | `run.download_to_dir(dir)` | files on disk |
| Every body, kept in the program | `run.download_to_memory()` | `list[DownloadedResult]` |
| The whole run as a single artifact | `run.download_all_results("out.zip")` | one `.zip` |
| One task's body (inside a loop) | `run.download_task_to_file(task, path)` / `run.download_task_to_memory(task)` | file / `bytes` |

**Bulk → disk.** Iterates results, fetches each body, writes one file
per task. Tunable parallelism, a progress bar, and safety caps that
raise `DownloadLimitExceeded` before a runaway job fills the disk:

```python
run.download_to_dir(
    "./out",
    status="successful",       # default; pass status=None for everything
    use_external_id=True,      # name files <external_id>.<ext> vs <task_id>.<ext>
    concurrency=8,             # parallel body fetches
    progress=True,             # tqdm bar (soft dep)
    max_files=20_000,          # caps that raise DownloadLimitExceeded
    max_bytes_per_file=10 * 1024 * 1024,
)
```

**Bulk → memory.** Same iteration, no disk — each item carries
`task_id`, `external_id`, `content_type`, and `body: bytes`:

```python
for r in run.download_to_memory(status="successful"):
    process(r.body, r.content_type)   # e.g. parse in-process, push to a queue
```

**Whole run → one zip.** A server-side export: kick off the zip, poll
until ready, stream it to disk in one call. Best when you want a single
artifact:

```python
run.download_all_results("results.zip")
```

> The server-side zip is capped at **1 GiB** per run. For larger runs —
> or one file per task — use `download_to_dir`, which fetches bodies
> client-side with **no size limit** (slower; tune with `concurrency=`).

**One task at a time.** When you're iterating `results()` and want only
some bodies (a Python-side filter the `status=` argument can't express),
download them individually — straight from each task's `result_url`:

```python
for task in run.results(status="successful"):
    if task.external_id in wanted:
        run.download_task_to_file(task, f"./out/{task.external_id}.html")
        body = run.download_task_to_memory(task)   # or just the raw bytes
```

`download_task_to_file` also accepts an open binary file object, not
just a path. Every result's `result_url` is a plain presigned URL, so
you can equally GET it with your own HTTP client (no auth header) — it's
TTL-limited, so fetch it promptly. See examples 02 and 08.

### Act on an id without a GET

Already have a job or run id (from a webhook, a queue, your own DB)?
`client.job(id)` / `client.run(id, run_id)` mint a `JobRef` / `RunRef` with
**no network call** — lifecycle ops act on the id directly; call `.load()`
for a loaded handle when you actually want the data:

```python
client.job(job_id).delete()          # DELETE — no preceding GET
client.job(job_id).run.stop()        # stop the current run
client.run(job_id, run_id).delete()  # scrub one specific run
client.job(job_id).load().data.status  # explicit GET → JobHandle
```

`get_job` / `get_run` remain the eager variants (a `client.job(id).load()`
in one call), for when you want the data up front. See example 10.

### Scheduled jobs & webhooks

Submit a recurring job with a typed schedule builder, then pause /
resume / re-schedule it via the `job.schedule` facet. (This is distinct
from `job.run.pause()`, which reversibly suspends the *current run's*
dispatcher — two different endpoints the API keeps separate.)

```python
from zenrows.batch import Rate

job = client.submit_scheduled(Rate(every=6, unit="hour"), ["https://example.com"])
job.schedule.pause()                            # stop firing (schedule keeps ticking)
job.schedule.resume()                           # fire again
job.schedule.update(Rate(every=1, unit="day"))  # replace the schedule
```

Register a completion webhook at submit time, or manage it later via
`PUT` / `DELETE /jobs/{id}/webhook`. Set `signature=True` to have each
delivery HMAC-signed (manage signing keys via the `/hmac/keys`
endpoints); it defaults to unsigned. Deliveries are at-least-once, so
dedup on the `X-ZenRows-Event-Id` header.

```python
client.submit_regular(
    ["https://example.com/a"],
    webhook={"url": "https://hooks.example.com/zr", "signature": True},
)
```

> No webhook receiver? Just poll for completion with `job.run.wait()` /
> `run.wait()`.

### Error handling

Every non-2xx surfaces as `BatchAPIError`; the `code` attribute carries
the stable code from the RFC 7807 body (`file_input_not_found`,
`idempotency_key_conflict`, etc.).

```python
from zenrows.batch import BatchAPIError

try:
    client.get_job("does-not-exist")
except BatchAPIError as exc:
    if exc.code == "not_found":
        ...
    raise
```

The full Batch surface (jobs, runs, tasks, results, content, history,
file_inputs, HMAC keys) is reachable via methods on `ZenRowsBatchClient`.
See `src/zenrows/batch/client.py` or `help(ZenRowsBatchClient)`.

## Contributing
Pull requests are welcome. For significant changes, please open an issue first to discuss what you would like to change.

## License
[MIT](./LICENSE)
