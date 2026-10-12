# Crawl first-install acceptance test: Python SDK

This folder tests the Python SDK (`zenrows` on PyPI) the way a new customer meets it. It builds the release
artifact, installs it in a clean container with nothing else, follows the README's Crawl
quickstart, and checks every Crawl feature against the live API. The result is one report.

It tests this repo only, so it runs on its own.

## What it needs

- bash, git and Docker. Nothing else on the host.
- Network access to GitHub, the package registries and the Crawl API.
- A Zenrows API key with Crawl access, in `ZENROWS_API_KEY`.

## How to run it

From the repo root:

```bash
export ZENROWS_API_KEY="$(cat path/to/key-file)"   # keep it out of your shell history
bash acceptance/crawl/run.sh              # build, run every check, write the report
bash acceptance/crawl/run.sh --no-key     # only the checks that need no key; the live checks are SKIP
bash acceptance/crawl/run.sh --report     # rebuild the report from out/
REF=v1.2.3 bash acceptance/crawl/run.sh   # test a tag, branch or SHA from GitHub instead of this checkout
```

Without `ZENROWS_API_KEY`, `run.sh` stops and asks for it. The report is
`acceptance/crawl/out/crawl-first-install-report.md`; `out/` is not committed.

Every input is an environment variable with a default in `versions.env`: `CRAWL_URL`
(default `https://www.scrapingcourse.com/ecommerce/`), `CRAWL_INCLUDE` (default `/product/`),
`REF`, `CRAWL_API_BASE`, the images (`node:20`, `python:3.12-slim`, `golang:1.25`, pinned by
digest) and the tool versions. `OUT_DIR` moves the output.

## What a run does

1. **Source.** By default, this checkout, including changes not yet committed. With `REF`, the
   repo at that ref from GitHub.
2. **Build.** In its own container, the release artifact: `pip install` of the wheel `python -m build` makes.
3. **Clean test container.** It gets only the artifact (read-only), the checks in `surface/`
   (read-only), the inputs, and the key by name (`-e ZENROWS_API_KEY`). It has no source, no
   caches from the build, and no tokens.
4. **README first.** It runs the README's Crawl quickstart as written. The only changes are the
   ones the cost rules need: the key, `CRAWL_URL`, `CRAWL_INCLUDE`, `max_pages` 10 or less, and
   no html. The report lists each change.
5. **Checks 1 to 21 and the extras.** Each writes one JSON result:
   `{surface, check, title, status, expected, actual, evidence}`, with `status` PASS, FAIL or
   SKIP. The evidence is the command and its trimmed output, with the key redacted.
6. **Clean-up.** It stops every crawl the run started and reads each one's final status and
   `coverage.pages_fetched`. It runs even when a check fails, or on Ctrl-C.
7. **Report.** Verdict, environment, install steps, feature matrix, findings, cost and
   clean-up. Last, the run checks that the key appears in no file under `out/`.

Some checks don't use the live API, so they behave the same on every run. They point the
client at a small local stand-in server that records what it sends. These are: "only the
fields you set are sent", "the error code is empty when the body has none", and the timing
and signal checks.

## The checks

| # | Feature | Expected |
| --- | --- | --- |
| 1 | Create | returns at once: `running`, a `crawl_id`, the echoed parameters; only the fields set are sent |
| 2 | Create with html | results get `content_status` and, once fetched, `content_url` |
| 3 | Idempotency | same key and body → same `crawl_id`; different body → 422 `idempotency_key_reused` (MCP, CLI: `CRAWL_INVALID_REQUEST` with the advice to send a new key) |
| 4 | Wait | returns the ended crawl; `failed` is returned, not raised (CLI: exit 1) |
| 5 | Wait runs out | a 1 s timeout on a running crawl returns it `running`, no error (CLI: exit 0; MCP, CLI: a `note`) |
| 6 | Get | `limit` 2, then the cursor: one page each; `next_cursor` moves on and is null on the last page |
| 7 | List | `limit` 2, then the next page: newest first; `next_cursor` absent or null on the last page |
| 8 | Results | every kept URL once, each matching `CRAWL_INCLUDE`; on a running crawl, what is kept so far (MCP, CLI: `partial: true`) |
| 9 | Content | by content id and by `content_url`: the same HTML |
| 10 | Download | a status, and one line per result with `url`, `content_status`, `content` |
| 11 | Stop | running → `stopped`, `stop_reason: user`; ended → answers as it ended |
| 12 | Not found | 404 `crawl_not_found` (MCP, CLI: `CRAWL_NOT_FOUND` with `crawl_id` and `server_code`) |
| 13 | Invalid input | `depth` 0 → 422 `invalid_parameter` (MCP, CLI: `CRAWL_INVALID_REQUEST`) |
| 14 | Gated option | `output_format` json → 422 `invalid_parameter`; if it is accepted, the key is in a beta segment, not a bug |
| 15 | Bad key | the key `invalid` → 401 (MCP, CLI: `AUTH_INVALID`) |
| 16 | Errors carry the server code | SDKs: `code` holds it, empty when the body has none; MCP, CLI: `server_code` |
| 17 | Naming | the API's operation and parameter names, in this surface's casing |
| 18 | Copy | Crawl is "new", never "beta"; one "still evolving" line; the 429 limit reads as the account's active jobs, shared with Batch |
| 19 | Failed crawl | an include pattern that matches nothing ends `failed` with `no_items_found`; SDKs return it, not raise it; MCP and CLI say `ok: false` (CLI: exit 1, `CRAWL_FAILED`) |
| 20 | Results after stop | the html crawl is stopped while its pages are still being fetched: reading its results ends at once (MCP and CLI: `partial: false`, no note to call again), and the same URLs come back once the pages in flight finish |
| 21 | Documented defaults | a create without `max_pages` and `max_items` gets the default the docs state (10) |

Extra checks for this surface: `mypy --strict` sees the package as typed (`py.typed`) and reads the real signatures.

## What it costs

Every crawl uses `depth` 1 and `max_pages` 10 or less. A run starts five crawls: the README run,
one URLs-only crawl, one idempotency crawl that is stopped at once, one html crawl that is
stopped with its pages in flight (check 20), and one that keeps nothing (check 19). On the default `CRAWL_URL`, a URLs-only crawl fetches 1 page and the
html crawl 6, so a run costs about **10 pages**, and at most 50 if every crawl reaches its
`max_pages`. Check 9 starts a sixth crawl, one html item, when the stop in check 20 lands
before any page is fetched. It adds at most 10 pages. The report lists each crawl id and its `pages_fetched`.

An account can run 3 active jobs at once, shared with Batch jobs. A run has at most 2 crawls
running at a time. If a create still gets a 429, the run waits `Retry-After` and tries again.
It never sends requests to trigger the limit. Don't run two copies against one account at the
same time.

## How to read the report

- **Verdict:** ready, unless there is a blocker or major finding.
- **Feature matrix:** PASS, FAIL or SKIP, with a short reason. SKIP means the check does not
  apply to this surface, the client rejects the input before the API, a crawl ended before
  the check could act on it, or there was no key.
- **Findings:** one entry per FAIL, most severe first, with the command and output. An install
  or README failure is a blocker; a wrong answer from a feature is major; naming, copy, the
  defaults and the extras are minor.
- `out/results/checks/*.json` holds every result; `out/results/container.log` is the full
  output.
