.PHONY: install sync test lint format typecheck check generate docs clean build

# Bootstrap: install + dev deps, build the local venv.
install sync:
	uv sync --all-extras

# Run the suite.
test:
	uv run pytest

# Static type check (ty — Astral). Shipped surface only; tests are
# covered by the suite. Generated models.py is excluded in pyproject.
typecheck:
	uv run ty check src

# Lint + format + type check (CI mode — no fixes).
check: typecheck
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

# Lint + format (writes fixes).
lint:
	uv run ruff check --fix src/ tests/
format:
	uv run ruff format src/ tests/

# Regenerate the pydantic v2 models from the backend's canonical spec.
# docs/openapi.yaml is the SDK-local copy of the spec; refresh it from the
# backend when the API changes.
# The HTTP client + facade are HAND-WRITTEN in src/zenrows/batch/client.py;
# only the type definitions come from this command.
generate:
	uv run datamodel-codegen \
		--input docs/openapi.yaml \
		--input-file-type openapi \
		--output src/zenrows/batch/models.py \
		--output-model-type pydantic_v2.BaseModel \
		--target-python-version 3.10 \
		--use-schema-description \
		--use-field-description \
		--use-double-quotes \
		--field-constraints \
		--use-standard-collections \
		--use-union-operator \
		--enum-field-as-literal one \
		--collapse-root-models \
		--use-annotated \
		--capitalise-enum-members \
		--reuse-model \
		--use-default

# Regenerate the markdown API reference (docs/batch-client-reference.md) from
# the SDK's docstrings via pydoc-markdown (ephemeral — no permanent dep). The
# builder relabels internal module headers to public section titles and strips
# the `zenrows.batch._x.` qualifiers, so the private `_module` layout never
# leaks into the customer-facing reference. See scripts/build_reference.py.
docs:
	@mkdir -p docs
	@uv run --with pydoc-markdown python scripts/build_reference.py > docs/batch-client-reference.md
	@echo "wrote docs/batch-client-reference.md ($$(wc -l < docs/batch-client-reference.md) lines)"

# Clean build artifacts + caches.
clean:
	rm -rf dist build *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Build wheel + sdist via hatchling.
build:
	uv build
