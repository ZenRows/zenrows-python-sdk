#!/usr/bin/env bash
# Crawl first-install acceptance test for this repo. See README.md in this folder.
#
#   ZENROWS_API_KEY=... bash run.sh    build the release artifact, run every check, write the report
#   bash run.sh --no-key                only the checks that need no key; the live checks are SKIP
#   bash run.sh --report                rebuild the report from the results already in out/
#
# Needs bash, git and Docker. The key reaches containers by name only (-e ZENROWS_API_KEY).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "$HERE/versions.env"
OUT_DIR="${OUT_DIR:-$HERE/out}"

no_key=0; report_only=0
for arg in "$@"; do
  case "$arg" in
    --no-key) no_key=1 ;;
    --report) report_only=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (expected --no-key or --report)" >&2; exit 2 ;;
  esac
done

if [ "$report_only" = 0 ] && [ "$no_key" = 0 ] && [ -z "${ZENROWS_API_KEY:-}" ]; then
  echo "ZENROWS_API_KEY is not set. Set it (a key with Crawl access), or run 'bash run.sh --no-key' for the offline checks." >&2
  exit 2
fi
if [ "$no_key" = 1 ]; then unset ZENROWS_API_KEY; fi
export ZENROWS_API_KEY="${ZENROWS_API_KEY:-}" CRAWL_URL CRAWL_INCLUDE CRAWL_API_BASE

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
CMDLINE="bash run.sh $*"
# Unique per run, so clean-up never touches the containers of another run.
LABEL="crawl-acceptance.run=$RUN_ID-$$-$RANDOM"
docker_run() { docker run --rm --label "$LABEL" "$@"; }

# ---- 1. Source: this checkout (default), or the repo on GitHub at $REF ----
prepare_source() {
  rm -rf "$OUT_DIR/build"; mkdir -p "$OUT_DIR/build/source"
  if [ -n "${REF:-}" ]; then
    log "source: ZenRows/$REPO@$REF from GitHub"
    docker_run -v "$OUT_DIR/build:/out" -v "$HERE/builders:/builders:ro" \
      -e REPO="$REPO" -e REF="$REF" "$NODE_IMAGE" bash /builders/fetch-source.sh >"$OUT_DIR/build/fetch.log" 2>&1
    echo "github:$REF" >"$OUT_DIR/build/origin"
  else
    local root; root="$(git -C "$HERE" rev-parse --show-toplevel)"
    log "source: this checkout ($root)"
    # Tracked and untracked-but-not-ignored files, as a release build would see them.
    (cd "$root" && git ls-files -co --exclude-standard -z | tar --null -T - -cf -) | tar -C "$OUT_DIR/build/source" -xf -
    local sha; sha="$(git -C "$root" rev-parse HEAD)"
    if [ -n "$(git -C "$root" status --porcelain -- . ":(exclude)$(realpath --relative-to="$root" "$HERE")")" ]; then
      sha="$sha+local-changes"
    fi
    echo "$sha" >"$OUT_DIR/build/sha"
    echo "checkout" >"$OUT_DIR/build/origin"
  fi
  # The test never reads the harness from the source; only the artifact goes in.
  rm -rf "$OUT_DIR/build/source/acceptance"
}

# ---- 2. Builder: the release artifact, in its own container ----
build_artifact() {
  mkdir -p "$OUT_DIR/build/artifact"
  case "$SURFACE" in
    node|mcp|cli)
      log "build: npm pack"
      docker_run -v "$OUT_DIR/build:/out" -v "$HERE/builders:/builders:ro" \
        -e SURFACE -e PNPM_VERSION "$NODE_IMAGE" bash /builders/build-npm.sh >"$OUT_DIR/build/build.log" 2>&1 ;;
    python)
      log "build: the wheel"
      docker_run -v "$OUT_DIR/build:/out" -v "$HERE/builders:/builders:ro" \
        -e PY_BUILD_VERSION "$PYTHON_IMAGE" bash /builders/build-python.sh >"$OUT_DIR/build/build.log" 2>&1 ;;
    go)
      # Go has no artifact: the clean container runs `go get` at a pushed commit, as a user would.
      local sha; sha="$(cat "$OUT_DIR/build/sha")"
      case "$sha" in *+local-changes)
        log "go: local changes are not tested; go get installs the commit ${sha%+local-changes} from GitHub" ;;
      esac
      echo "${sha%+local-changes}" >"$OUT_DIR/build/go_ref" ;;
  esac
}

# ---- 3. Clean test container: only the artifact, the checks and the inputs go in ----
run_checks() {
  rm -rf "$OUT_DIR/results"; mkdir -p "$OUT_DIR/results"
  local image
  case "$SURFACE" in node|mcp|cli) image="$NODE_IMAGE" ;; python) image="$PYTHON_IMAGE" ;; go) image="$GO_IMAGE" ;; esac
  log "checks: a clean $image"
  local rc=0
  docker_run --init \
    -v "$OUT_DIR/build/artifact:/artifact:ro" \
    -v "$HERE/surface:/harness:ro" \
    -v "$OUT_DIR/results:/results" \
    -e ZENROWS_API_KEY -e CRAWL_URL -e CRAWL_INCLUDE -e CRAWL_API_BASE \
    -e SURFACE -e GO_REF="$(cat "$OUT_DIR/build/go_ref" 2>/dev/null || true)" \
    -e MCP_SDK_VERSION -e TYPESCRIPT_VERSION -e MYPY_VERSION \
    -w /work --tmpfs /work:exec,size=2g \
    "$image" bash /harness/run-in-container.sh >"$OUT_DIR/results/container.log" 2>&1 || rc=$?
  log "checks: the container exited $rc (out/results/container.log)"
}

# ---- 4. Clean-up: stop every crawl this run started. Runs even when a check fails. ----
cleanup() {
  [ "$report_only" = 1 ] && return 0
  docker ps -q --filter "label=$LABEL" | xargs -r docker kill >/dev/null 2>&1 || true
  if [ -n "${ZENROWS_API_KEY:-}" ] && [ -d "$OUT_DIR/results" ]; then
    log "clean-up: stopping every crawl this run started"
    docker_run -v "$OUT_DIR:/out" -v "$HERE/tools:/tools:ro" \
      -e ZENROWS_API_KEY -e CRAWL_API_BASE "$PYTHON_IMAGE" python -I /tools/cleanup.py /out || log "clean-up failed"
  fi
}

report() {
  log "report: out/crawl-first-install-report.md"
  docker_run -v "$OUT_DIR:/out" -v "$HERE/tools:/tools:ro" \
    -e CMDLINE="$CMDLINE" -e SURFACE -e REPO -e CRAWL_URL -e CRAWL_INCLUDE -e CRAWL_API_BASE \
    -e NODE_IMAGE -e PYTHON_IMAGE -e GO_IMAGE -e MCP_SDK_VERSION -e TYPESCRIPT_VERSION \
    -e MYPY_VERSION -e PY_BUILD_VERSION -e PNPM_VERSION \
    "$PYTHON_IMAGE" python -I /tools/report.py /out
  # The key must appear in nothing the run wrote. Checked by value, inside a container.
  if [ -n "${ZENROWS_API_KEY:-}" ]; then
    docker_run -v "$OUT_DIR:/out" -v "$HERE/tools:/tools:ro" -e ZENROWS_API_KEY "$PYTHON_IMAGE" \
      python -I /tools/scan_key.py /out
  fi
}

export SURFACE REPO NODE_IMAGE PYTHON_IMAGE GO_IMAGE PNPM_VERSION PY_BUILD_VERSION \
  MCP_SDK_VERSION TYPESCRIPT_VERSION MYPY_VERSION
if [ "$report_only" = 1 ]; then report; exit 0; fi

mkdir -p "$OUT_DIR"
echo "$RUN_ID" >"$OUT_DIR/run_id"
trap cleanup EXIT
trap 'exit 130' INT TERM
if prepare_source && build_artifact; then
  run_checks
else
  log "the source or build step failed (out/build/*.log)"
  mkdir -p "$OUT_DIR/results"
  docker_run -v "$OUT_DIR:/out" -v "$HERE/tools:/tools:ro" -e SURFACE "$PYTHON_IMAGE" python -I /tools/build_failed.py /out
fi
cleanup
trap - EXIT
report
log "done: $OUT_DIR/crawl-first-install-report.md"
