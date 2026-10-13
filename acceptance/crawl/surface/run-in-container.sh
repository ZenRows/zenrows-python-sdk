#!/usr/bin/env bash
# Entry point in the clean python container: install the wheel as `pip install zenrows` would,
# install the pinned mypy for the typing check, record the versions, run the checks.
set -uo pipefail
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore PIP_NO_CACHE_DIR=1
pip install /artifact/*.whl > /results/pip-install.log 2>&1 || { cat /results/pip-install.log; exit 1; }
pip install "mypy==$MYPY_VERSION" > /results/pip-mypy.log 2>&1
printf '{"python":"%s","pip":"%s","mypy":"%s","os":"%s"}\n' "$(python -V 2>&1)" "$(pip -V | cut -d' ' -f2)" \
  "$MYPY_VERSION" "$(. /etc/os-release; echo "$PRETTY_NAME")" > /results/env.json
cd /work && python /harness/test.py
