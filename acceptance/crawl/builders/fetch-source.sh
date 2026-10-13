#!/usr/bin/env bash
# Runs in the Node image when REF is set: downloads ZenRows/$REPO at $REF as GitHub's source
# tarball and reads the full commit SHA from it.
set -euo pipefail
cd /out
curl -fsSL --retry 4 -o src.tar.gz "https://codeload.github.com/ZenRows/$REPO/tar.gz/$REF"
# git get-tar-commit-id stops reading after the header, so the pipe breaks: ignore that.
(gunzip -c src.tar.gz | git get-tar-commit-id > sha) || true
[ -s sha ] || { echo "no commit id in the tarball of $REPO@$REF" >&2; exit 1; }
rm -rf source && mkdir -p source && tar -xzf src.tar.gz -C source --strip-components 1 && rm src.tar.gz
echo "fetched $REPO@$REF = $(cat sha)"
