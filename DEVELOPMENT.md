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
├── __init__.py               # re-exports both clients
├── client.py                 # ZenRowsClient (legacy sync scraper)
└── batch/
    ├── __init__.py           # ZenRowsBatchClient + key models
    ├── client.py             # hand-written typed facade
    ├── _transport.py         # httpx wrapper, RFC 7807 → exceptions
    ├── errors.py             # BatchAPIError, ProblemDetail
    └── models.py             # GENERATED — pydantic v2 (do not edit)
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

## Common tasks

| Make target     | What it does |
|-----------------|--------------|
| `make sync`     | `uv sync --all-extras` |
| `make test`     | `uv run pytest` |
| `make check`    | `ty check` + `ruff check` + `ruff format --check` (CI mode) |
| `make typecheck`| `ty check src` (static types; `models.py` excluded) |
| `make lint`     | `ruff check --fix` |
| `make format`   | `ruff format` |
| `make generate` | Re-emit `src/zenrows/batch/models.py` from `docs/openapi.yaml` |
| `make build`    | Build wheel + sdist via hatchling |
| `make clean`    | Drop caches + build outputs |

## Refreshing the OpenAPI spec

`docs/openapi.yaml` is the SDK-local copy of the spec. `make generate`
reads it to emit the models. To refresh after a backend spec change:

1. Copy the updated spec into `docs/openapi.yaml`.
2. Run `make generate`.
3. Run `make check && make test`.
4. If the wire shape changed, update `src/zenrows/batch/client.py`
   so the facade method signatures still typecheck.

## Publishing

```bash
make clean
make build                          # produces dist/*.whl + dist/*.tar.gz
uv run twine upload dist/*          # or test PyPI:
uv run twine upload --repository testpypi dist/*
```

Bump `version` in `pyproject.toml` and `src/zenrows/__version__.py`
together before each release.
