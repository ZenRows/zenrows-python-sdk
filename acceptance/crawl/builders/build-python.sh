#!/usr/bin/env bash
# Runs in the Python image. Builds the wheel a release would publish. Only the wheel leaves.
set -euo pipefail
cp -a /out/source /build && cd /build
pip install --quiet --disable-pip-version-check --root-user-action=ignore "build==$PY_BUILD_VERSION"
python -m build --wheel --outdir /out/artifact
ls -l /out/artifact
