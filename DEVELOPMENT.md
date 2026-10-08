# Development

This project uses [uv](https://docs.astral.sh/uv/) for env + dependencies,
[ruff](https://docs.astral.sh/ruff/) for lint + format, and
[ty](https://docs.astral.sh/ty/) for static type checking. Source lives
under `src/zenrows/`; tests under `tests/`.

## First-time setup

```bash
uv sync --all-extras
```

Creates `.venv/` and installs everything in `pyproject.toml`, including
dev tools. After that, prefix commands with `uv run …` or use the
Makefile targets below.

## Layout

```
src/zenrows/
├── __init__.py               # re-exports the clients
├── client.py                 # ZenRowsClient (legacy sync scraper)
├── batch/
│   ├── __init__.py           # ZenRowsBatchClient + key models
│   ├── client.py             # hand-written typed facade
│   ├── _transport.py         # httpx wrapper, RFC 7807 → exceptions
│   ├── errors.py             # BatchAPIError, ProblemDetail
│   └── models.py             # GENERATED — pydantic v2 (do not edit)
└── crawl/
    ├── __init__.py           # ZenRowsCrawlClient + models
    ├── client.py             # hand-written typed facade
    ├── errors.py             # CrawlAPIError
    └── models.py             # hand-written pydantic v2 models
```

The Batch SDK is split deliberately:

| File           | Owner             | Regenerate?       |
|----------------|-------------------|-------------------|
| `models.py`    | datamodel-codegen | `make generate`   |
| `client.py`    | hand-written      | never auto        |
| `_transport.py`| hand-written      | never auto        |
| `errors.py`    | hand-written      | never auto        |

This way the wire types stay in lockstep with the OpenAPI document
while the ergonomic surface (method names, helpers, retries, URL
override) stays in our control.

The Crawl client uses the shared HTTP transport (`_transport.py`: key
header, retries, problem+json) with `CrawlAPIError.from_response` as
its error mapping, and the shared `poll_until` loop. Its models are
hand-written: the surface is a few small schemas, so there is no Crawl
codegen step.
Keep them tolerant: unknown fields are ignored, and every response enum
gets `_missing_ = classmethod(open_enum_missing)`.

## Common tasks

| Make target     | What it does |
|-----------------|--------------|
| `make sync`     | `uv sync --all-extras` |
| `make test`     | `uv run pytest` (offline; e2e tests deselected) |
| `make test-e2e` | End-to-end tests against a live API (see below) |
| `make check`    | `ty check` + `ruff check` + `ruff format --check` (CI mode) |
| `make typecheck`| `ty check src` (static types; `models.py` excluded) |
| `make lint`     | `ruff check --fix` |
| `make format`   | `ruff format` |
| `make generate` | Re-emit `src/zenrows/batch/models.py` from `docs/openapi.yaml` |
| `make build`    | Build wheel + sdist via hatchling |
| `make clean`    | Drop caches + build outputs |

## Running the Crawl e2e test

`tests/e2e/test_crawl_e2e.py` drives a real crawl through the client:
create (with `include_patterns`, `output_format="html"`), wait, read the
results, one page and the NDJSON download, list, stop, and a 404. It
costs a few credits. It is marked `e2e`, which `make test` deselects,
and it skips unless both variables below are set.

Set `ZENROWS_API_KEY` to a key with Crawl access, and
`ZENROWS_CRAWL_BASE_URL` to the API base (e.g.
`https://api.zenrows.com/v1`). Then run `make test-e2e`:

```bash
export ZENROWS_API_KEY=zr_...                       # a key with Crawl access
export ZENROWS_CRAWL_BASE_URL=https://api.zenrows.com/v1
make test-e2e
```

To test against a local or staging deployment, point
`ZENROWS_CRAWL_BASE_URL` at its `/v1` base instead. When the account
has too many crawls running (429 `too_many_crawls`), the test waits and
retries (up to 5 minutes) before creating its crawl.

## Refreshing the OpenAPI spec

`docs/openapi.yaml` is the SDK-local copy of the spec. `make generate`
reads it to emit the models. To refresh after a backend spec change:

1. Copy the updated spec into `docs/openapi.yaml`.
2. Run `make generate`.
3. Run `make check && make test`.
4. If the wire shape changed, update `src/zenrows/batch/client.py`
   so the facade method signatures still typecheck.

## Open enums (generated)

docs/openapi.yaml marks the response enums `x-extensible-enum: true`: the
server may add values at any time. After datamodel-codegen runs,
`make generate` calls `scripts/open_extensible_enums.py`, which matches each
extensible schema in the spec to its generated Enum (by value set) and adds

```python
_missing_ = classmethod(open_enum_missing)
```

from `src/zenrows/batch/_open_enum.py`. An unknown value becomes a cached
`UNKNOWN` pseudo-member keeping the raw value (serializes back verbatim,
hashable, picklable, absent from iteration). Enums without the flag
(request side, e.g. `JobType`) stay strict, so a typo still fails locally.
The step is a script because datamodel-codegen does not expose schema
extensions to enum templates.

`tests/test_open_enums.py` derives both sets from the spec and fails if a
regeneration drops the hook or applies it to a strict enum.

## Publishing

```bash
make clean
make build                          # produces dist/*.whl + dist/*.tar.gz
uv run twine upload dist/*          # or test PyPI:
uv run twine upload --repository testpypi dist/*
```

Bump `version` in `pyproject.toml` and `src/zenrows/__version__.py`
together before each release.
