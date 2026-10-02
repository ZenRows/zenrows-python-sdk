.PHONY: install sync test lint format typecheck check generate docs clean build bump release

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
# Open enums: scripts/open_extensible_enums.py then adds a `_missing_` hook
# (src/zenrows/batch/_open_enum.py) to every enum the spec marks
# `x-extensible-enum: true`, so a value the server adds later parses as an
# UNKNOWN member keeping the raw value. Other (request-side) enums stay strict.
# tests/test_open_enums.py fails if a regeneration drops the hook.
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
	uv run python scripts/open_extensible_enums.py docs/openapi.yaml src/zenrows/batch/models.py

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

# Bump the version (pyproject.toml + src/zenrows/__version__.py) and relock.
# PART defaults to patch; pass PART=minor, PART=major, or an explicit X.Y.Z.
# Local-only: stages the change but does not commit, push, or tag.
PART ?= patch
bump:
	uv run python scripts/bump_version.py $(PART)
	uv lock
	git add pyproject.toml src/zenrows/__version__.py uv.lock

# Bump, commit, push to main, and cut the GitHub release that triggers the
# PyPI publish workflow (.github/workflows/release.yml). Pushes to main and
# publishes a public release — confirm the diff before running this.
release: bump
	$(eval NEW_VERSION := $(shell grep -m1 '^version = ' pyproject.toml | sed -E 's/version = "(.*)"/\1/'))
	git commit -m "chore: bump version to $(NEW_VERSION)"
	git push origin main
	gh release create "v$(NEW_VERSION)" --title "v$(NEW_VERSION)" --generate-notes
