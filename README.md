<p align="center">
    <picture>
        <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo/dark.svg"/>
        <img alt="Zenrows Logo" src=".github/assets/logo/light.svg" width="300" />
    </picture>
</p>

# Zenrows Python SDK

SDK to access [Zenrows](https://www.zenrows.com/) APIs directly from Python.
Zenrows handles proxies rotation, headless browsers, and CAPTCHAs for you.

This package ships three clients:

  - **`ZenRowsClient`** — the original synchronous scraping client.
    One URL in, one HTML/JSON response out. Best for ad-hoc scraping.
  - **`ZenRowsBatchClient`** — the async-job / Batch API client. Submit
    thousands of URLs as one job, poll for results, optionally upload a
    CSV of URLs in one call. Best for offline / bulk pipelines.
  - **`ZenRowsCrawlClient`** — the Crawl API client. Give it one start
    URL; it follows links and returns the URLs (and, optionally, the
    pages) it finds behind it. _(New.)_

## Table of Contents

- [Installation](#installation)
- [Quickstart — synchronous scraping (`ZenRowsClient`)](#quickstart--synchronous-scraping-zenrowsclient)
  - [Adaptive Stealth Mode](#adaptive-stealth-mode)
  - [POST Requests](#post-requests)
  - [PUT Requests](#put-requests)
  - [Concurrency](#concurrency)
  - [Extract](#extract)
    - [Preparing a domain](#preparing-a-domain)
- [Quickstart — Batch API (`ZenRowsBatchClient`)](#quickstart--batch-api-zenrowsbatchclient)
  - [Extract in a batch](#extract-in-a-batch)
  - [Upload URLs from a CSV](#upload-urls-from-a-csv)
  - [Estimate cost before submitting](#estimate-cost-before-submitting)
  - [Retry only the failed tasks](#retry-only-the-failed-tasks)
  - [Download results](#download-results)
  - [Act on an id without a GET](#act-on-an-id-without-a-get)
  - [Scheduled jobs & webhooks](#scheduled-jobs--webhooks)
  - [Error handling](#error-handling)
- [Quickstart — Crawl API (new) (`ZenRowsCrawlClient`)](#quickstart--crawl-api-new-zenrowscrawlclient)
  - [Read the pages](#read-the-pages)
  - [List and stop crawls](#list-and-stop-crawls)
  - [Crawl errors](#crawl-errors)
- [Contributing](#contributing)
- [License](#license)

## Installation

```bash
pip install zenrows
```

## Quickstart — synchronous scraping (`ZenRowsClient`)
Start using the API by [creating your API Key](https://www.zenrows.com/register?p=free).

The SDK uses [requests](https://docs.python-requests.org/) for HTTP requests. The client's response will be a requests `Response`.

It also uses [Retry](https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html) to automatically retry failed requests (status codes 422, 429, 500, 502, 503, and 504, except Extract's `422 no_data_extracted`). Retries are not active by default; you need to specify the number of retries, as shown below. It already includes an exponential back-off retry delay between failed requests.

`client.fetch()` is the primary method for the main page-scraping product; `client.get()` still works and is kept as a deprecated alias.

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY", retries=1)
url = "https://www.zenrows.com/"

response = client.fetch(url, params={
    # Deprecated: use client.extract() for structured data instead
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

[Extract](https://docs.zenrows.com/extract/introduction) runs a page through Zenrows' AI-powered structured extraction instead of returning raw HTML. Use `client.extract()`: it's the same request as `fetch()`, with the `extract` param set for you (defaults to `"auto"`; pass `"native"` or `"standard"` for the other contracts).

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY")
url = "https://www.zenrows.com/"

response = client.extract(url)  # extract: "auto"
# response = client.extract(url, mode="native")

print(response.json())
```

`extract()` also sends [Adaptive Stealth Mode](https://docs.zenrows.com/fetch/features/adaptive-stealth-mode) (`mode: "auto"`) by default, except when `params` sets `js_render` or `premium_proxy`: the API rejects either combined with Adaptive Stealth Mode, so your fixed configuration is sent as is. Pass `adaptive_stealth=False` to turn it off entirely.

Extract works on any prepared domain (you can prepare one yourself with `POST /v1/extract/prepared-domains`). A `403` with `code: "REQS007"` means Extract can't extract this page. Check `reason` in the problem body when present; older responses may not carry it:

- `domain_not_prepared`: prepare the domain with `client.prepare_domain()` (see below), poll `client.prepared_domain_status()` until `status` is `"ready"`, then retry.
- `page_not_recognised`: the domain is served, but this page isn't one Extract extracts (a home, search, login or category page). Try a product, article or listing page, or retry once in a few minutes. Preparing the domain again changes nothing.
- `domain_not_enabled`: preparing doesn't help. Use `css_extractor`, or contact support.

```python
response = client.extract(url)
if response.status_code == 403:
    problem = response.json()
    if problem.get("code") == "REQS007":
        reason = problem.get("reason")  # None on older responses
        if reason == "domain_not_prepared":
            ...  # client.prepare_domain(...), then poll client.prepared_domain_status(...)
        elif reason == "page_not_recognised":
            ...  # try a product, article or listing page
        elif reason == "domain_not_enabled":
            ...  # use css_extractor, or contact support
```

The legacy `402` with `code: "AUTH010"` means the domain isn't enabled. `extract()` returns both error responses to you as is, in one request, and never retries them with autoparse. `fallback_to_autoparse` is deprecated and has no effect; passing `True` emits a `DeprecationWarning`.

#### Preparing a domain

`client.prepare_domain(domain, example_urls)` submits a domain for Extract (`POST /v1/extract/prepared-domains`), and `client.prepared_domain_status(domain)` reads where it is (`GET /v1/extract/prepared-domains/{domain}`). Both return the parsed status object. Pass the domain without a scheme and 1 to 5 example URLs on it. These calls cost no credits.

One example URL works; we recommend 3 or more of the same page type (e.g. 3 product pages), because Extract compares pages of one type to check the fields it found and fill in fields one page is missing. Several page types can share the 5 (e.g. 3 product pages + 2 search-result pages).

Preparation takes minutes, and the client does not wait for it. Poll the status until it is no longer `"preparing"`:

```python
import time

from zenrows import PreparedDomainError, ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY")

try:
    status = client.prepare_domain(
        "www.scrapingcourse.com",
        [
            # Three pages of one page type: product pages on the same domain.
            "https://www.scrapingcourse.com/ecommerce/product/abominable-hoodie/",
            "https://www.scrapingcourse.com/ecommerce/product/adrienne-trek-jacket/",
            "https://www.scrapingcourse.com/ecommerce/product/aeon-capri/",
        ],
    )
except PreparedDomainError as err:
    # e.g. 409 in_progress, 429 out_of_allowance, 503 overloaded
    print(err.status_code, err.code, err.detail)
    raise

while status["status"] == "preparing":
    time.sleep(30)
    status = client.prepared_domain_status("www.scrapingcourse.com")

print(status["status"])  # "ready" or "failed" (see status["failure"])
```

`prepare_domain_async()` and `prepared_domain_status_async()` are the async counterparts. The client's `retries` setting doesn't apply to these calls, and an empty `domain` raises `ValueError` before any request.

Both methods raise `PreparedDomainError` on an error answer, with `status_code`, `code` (`invalid_argument`, `not_found`, `in_progress`, `out_of_allowance`, `overloaded`, `disabled`, ...), `detail`, `reason` and the whole `problem` body. See the [Extract endpoints docs](https://docs.zenrows.com/extract/endpoints) for every status and failure code.

A `422` with `code: "no_data_extracted"` means Extract knows this kind of page but found no data on it. It isn't charged, and the client never retries it, even with `retries` set. `is_no_data_extracted(response)` tells you when you got one. See the [Extract docs](https://docs.zenrows.com/extract/setup).

## Quickstart — Batch API (`ZenRowsBatchClient`)

> Batch is open to every account and spends the same credits as a regular
> Fetch request.

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

`run.wait()` returns once the run is terminal (`completed`, `stopped` or
`failed`; read `failure_reason` on a failed run). A paused run never
progresses, so the waiter raises `RunPaused` (carrying the run) instead
of polling until its timeout; pass `wait_while_paused=True` to keep
polling.

> Need full control over the request body? `client.submit_job({...})` accepts a raw dict (the
> wire shape) or a typed `SubmitJobRequest`, and returns the same `JobRef`.

### Extract in a batch

Set `extract` in `zenrows_params` to run tasks through Extract — structured data
instead of raw HTML. It works job-wide or per task, and per-task values win on
collision.

```python
job = client.submit_regular(
    [
        # Every task in this job runs through Extract...
        {"url": "https://example.com/products", "external_id": "p1"},
        # ...unless it overrides the job-level params.
        {"url": "https://example.com/raw", "zenrows_params": {}},
    ],
    zenrows_params={"extract": "auto"},
)
```

An Extract task's result carries two keys — `html` (the raw page) and `parsed`
(the structured data). It costs the same as a regular task, so `estimate_cost`
prices it correctly. `extract_fields` narrows the returned fields, as on
the regular API.

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

After a run finishes, read the credits it used from `run.stats.spend`
(`{credits, cost}`), an indicative figure; your account's usage page is
authoritative. Failed tasks are not charged, except target `404` and
`410` responses, which are billed as on the regular API. Result rows
carry no per-task spend.

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

Start a rerun only once the previous run is terminal, or the API answers
`409 run_not_terminal`. A failed-only (partial) rerun works within 10
days of the start of the run it retries; after that it raises
`409 partial_rerun_window_expired`, and a full `rerun()` still works.

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

> The server caps one export at **100 GiB** of results. For larger runs,
> or one file per task, use `download_to_dir`, which fetches bodies
> client-side with no export cap (slower; tune with `concurrency=`).

**Expired results.** Results are kept for 14 days by default. After that
a successful task reads as `expired`, has no `result_url`, and still
matches `status="successful"`. The bulk helpers skip such rows (and
failed ones) instead of stopping, log a WARNING on
`zenrows.batch.download` with the counts, and pass each one to
`on_skip` if you set it:

```python
from zenrows.batch import TaskStatus

skipped = []
run.download_to_dir("./out", on_skip=skipped.append)
expired = [r for r in skipped if r.status is TaskStatus.EXPIRED]
```

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
you can equally GET it with your own HTTP client (no auth header). It is
valid for 2 hours; list the results again for a fresh one. See examples
02 and 08.

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

Register a webhook at submit time, or manage it later with
`job.set_webhook(url, signature=...)` / `job.delete_webhook()`. You get
one event per run: `run.completed` when it finishes on its own, or
`run.failed` when an account error (credits, subscription, key cap)
ends it. A run you stop yourself sends none. A failed delivery is
retried with backoff for up to 3 days, so an event can arrive more than
once: deduplicate on the `X-ZenRows-Event-Id` header.

Set `signature=True` to have each delivery HMAC-signed; it defaults to
unsigned. Create the signing key first with `client.rotate_hmac_key()`
(the first key is active at once; a later rotation stages a new key you
promote with `finalize_hmac_key()`), and store the `secret` it returns:
it is shown once, base64-encoded. `verify_webhook_signature` checks the
`X-Signature` header (`t=...,v1=...,kid=...`): it decodes the secret,
recomputes HMAC-SHA256 over `<t>.<raw body>`, compares in constant time
and rejects timestamps more than 5 minutes off.

```python
from zenrows.batch import WEBHOOK_SIGNATURE_HEADER, WebhookEvent, verify_webhook_signature

client.submit_regular(
    ["https://example.com/a"],
    webhook={"url": "https://hooks.example.com/zr", "signature": True},
)

# kid -> the base64 secret rotate_hmac_key() returned
SECRETS = {"<kid>": "<secret>"}


def receive(raw_body: bytes, headers) -> int:
    # Verify the raw bytes before parsing them.
    if not verify_webhook_signature(raw_body, headers[WEBHOOK_SIGNATURE_HEADER], SECRETS):
        return 401
    event = WebhookEvent.model_validate_json(raw_body)
    print(event.event_type, event.job_id, event.run_id)
    return 200
```

During a key rotation, pass both secrets keyed by their `kid`; each
delivery names the key that signed it.

> No webhook receiver? Just poll for completion with `job.run.wait()` /
> `run.wait()`.

### Error handling

Every non-2xx surfaces as `BatchAPIError`; the `code` attribute carries
the stable code from the RFC 7807 body (`file_input_not_found`,
`idempotency_key_conflict`, `api_key_cap_reached`, etc.) and `detail` the
human-readable explanation (for a credit cap: which cap, and when it resets).
`BatchErrorCode` names every code; its members compare equal to `code`.

```python
from zenrows.batch import BatchAPIError, BatchErrorCode

try:
    client.get_job("does-not-exist")
except BatchAPIError as exc:
    if exc.code == BatchErrorCode.NOT_FOUND:
        ...
    raise
```

The client retries network errors and `429` / `502` / `503` / `504` on
requests that are safe to repeat: reads, and a submit, rerun or
`add_tasks` call that carries an `idempotency_key`. Within 24 hours a
repeat with the same key and body returns the first answer instead of
doing the work twice. A `429 quota_exceeded` (3 active jobs per account,
shared with Crawl, or 5 scheduled jobs) is not retried: it clears only
when a job finishes, is closed or is deleted.

```python
import uuid

job = client.submit_open(["https://example.com/a"])
job.add_tasks(
    {"tasks": [{"url": "https://example.com/b"}], "last_batch": True},
    idempotency_key=str(uuid.uuid4()),  # one key per logical call, reused on retry
)
```

### Forward-compatible enums

Every enum the Batch API returns (`RunStatus`, `FailureReason`, ...) is open:
a value the server adds after your SDK version was released parses as an
`UNKNOWN` member whose `.value` is the raw string, instead of failing the
whole response. Branch on the members you know and keep a fallback:

```python
from zenrows.batch.models import FailureReason

run = client.get_run(job_id, run_id=run_id).data
if run.failure_reason is FailureReason.API_KEY_CAP_REACHED:
    print(run.failure_detail)  # which cap was reached and when it resets
elif run.failure_reason is not None:
    print("failed:", run.failure_reason.value)  # includes values unknown to this SDK
```

Runs that fail because an API key reached its credit cap carry
`failure_reason = "api_key_cap_reached"`. Older SDK versions without open enums cannot
parse such runs, so upgrade before enabling API key credit caps with Batch.

The full Batch surface (jobs, runs, tasks, results, content, history,
file_inputs, HMAC keys) is reachable via methods on `ZenRowsBatchClient`.
See `src/zenrows/batch/client.py` or `help(ZenRowsBatchClient)`.

## Quickstart — Crawl API (new) (`ZenRowsCrawlClient`)

Crawl is still evolving: new features are coming, limits may be tuned, and the changelog announces each change.

A crawl starts from one URL, follows the links on each page, and keeps
the URLs that match your patterns, up to `max_items`. It stops at
`max_items` or `max_pages`, or when no link is left. `depth` limits the
link hops from the start URL; without it the crawl has no depth limit.
It stays on the start URL's registrable domain;
subdomains count. It runs in the background: `create` returns at once with
`status="running"`, and `wait` blocks until it ends. Without
`output_format` a crawl returns URLs only; `output_format="html"` or
`output_format="markdown"` also returns each kept URL's page, as HTML or
as Markdown.

```python
import os
from zenrows import ZenRowsCrawlClient

client = ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])

crawl = client.create(
    "https://www.scrapingcourse.com/ecommerce/",
    depth=1,                         # link hops from the start URL (default: no limit)
    max_items=50,                    # stop after keeping 50 URLs (default 100)
    max_pages=100,                   # stop after fetching 100 pages (default 100)
    include_patterns=["/product/"],  # keep only URLs containing this
)
crawl = client.wait(crawl.crawl_id)            # polls up to timeout=600 s
print(crawl.status.value, crawl.stop_reason, crawl.coverage)

for result in client.results(crawl.crawl_id):  # follows next_cursor
    print(result.url)
```

`wait` returns the crawl when it ends, or when `timeout` (seconds,
default 600) runs out. It does not raise: on timeout the crawl is still
`running` (call `wait` again, or `stop`), and a `failed` crawl comes
back with `crawl.error` set. A crawl whose pages held nothing new to
follow (no link, only links to other sites, or only links the patterns
reject) ends `completed` with no results and no `error`: check the
patterns against the URLs the start page links to. A crawl that ran
out of credits, or whose API key hit a credit cap, ends `stopped` with `crawl.error.code`
`insufficient_credits` or `api_key_cap_reached` and no `stop_reason`; the
pages it fetched before that stay readable. `results` does not poll: it returns at the first empty page, so on a
running crawl it yields only the URLs kept so far. Call `wait` first to
read every result. `results(crawl_id, limit=...)` sets the page size of
each request. `get(crawl_id, cursor=..., limit=...)` returns one raw page.

The client takes these options: `base_url` (default
`https://api.zenrows.com/v1`), `retries` (default 3; set 0 to disable)
and `timeout` (seconds per request, default 30):

```python
client = ZenRowsCrawlClient(api_key, base_url="https://api.zenrows.com/v1", retries=3, timeout=30)
```

### Read the pages

Pass `output_format="html"` to also fetch the page of every kept URL:

```python
crawl = client.create(start_url, depth=1, output_format="html")
client.wait(crawl.crawl_id)

for result in client.results(crawl.crawl_id):
    if result.content_url:                       # content_status == fetched
        html = client.content(crawl.crawl_id, result)

# Or everything in one NDJSON file: {"url", "content_status", "content"} per line.
with client.download(crawl.crawl_id) as download:  # streamed, parsed
    print(download.status)                         # from X-Crawl-Status
    for line in download.lines:
        print(line.url, line.content_status)
```

`content` takes a result, its `content_url`, or its content id.
`download` returns a `CrawlDownload`: its `status` is the crawl's status
when the file was read, and its `lines` yields parsed lines. On a `running` crawl the
file holds only the results kept so far.

### List and stop crawls

```python
page = client.list(limit=20)            # newest first, without results
for c in page.crawls:
    print(c.crawl_id, c.status.value)
# Pass page.next_cursor as cursor= for the next page; it is None on the last.

client.stop(crawl_id)                   # idempotent; read final counts with get()
```

### Crawl errors

Every non-2xx raises `CrawlAPIError`. Branch on `status_code` and `code`:

```python
from zenrows import CrawlAPIError
from zenrows.crawl import CRAWL_NOT_ENABLED_CODE

try:
    client.create(start_url, depth=1)
except CrawlAPIError as exc:
    if exc.code == CRAWL_NOT_ENABLED_CODE:  # 403 REQS008
        print("Crawl is not enabled for this account")
    elif exc.code == "too_many_crawls": # 429: active jobs limit reached
        print("retry in", exc.retry_after, "s")
    elif exc.status_code in (400, 422):
        print(exc.detail)
    else:
        raise
```

| Status | `code` | What to do |
|---|---|---|
| 400 | `invalid_request`, `unknown_parameter`, `invalid_cursor` | Fix the request. Do not retry it as is. |
| 403 | `REQS008` | Crawl is not enabled for the account (`CRAWL_NOT_ENABLED_CODE`). |
| 404 | `crawl_not_found`, `content_not_found` | Check the id. |
| 409 | `idempotency_request_in_flight` | Retry after the first request with this key finishes. |
| 422 | `invalid_parameter`, `invalid_start_url`, `domain_not_allowed` | Fix the request. Do not retry it as is. |
| 422 | `idempotency_key_reused` | Use a new key, or no key. Do not retry it as is. |
| 429 | `too_many_crawls` | The account has reached its limit of active jobs (3 by default), shared with its Batch jobs. Retry after `exc.retry_after` seconds. `create` does not retry it. |
| 503 | `crawl_busy` | `stop` could not be saved yet; the crawl is still running. `stop` retries it up to `retries` times, waiting `exc.retry_after` seconds, then raises it. Repeating the stop is safe. |

`code` is None when the response has no problem body with a code (for
example a 502 from a proxy). A crawl that ended `failed`, or `stopped` on
credits, is not an error response: read `crawl.error.code` and
`crawl.error.detail` on it. Response enums (`CrawlStatus`, `StopReason`,
`ContentStatus`, ...) are open: a value added later parses as an
`UNKNOWN` member that keeps the raw value.

## Contributing
Pull requests are welcome. For significant changes, please open an issue first to discuss what you would like to change.

## License
[MIT](./LICENSE)
