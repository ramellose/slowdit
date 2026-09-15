#!/bin/sh
# slowdit's test suite, as a test-convention entry point.
#
# slowdit's default test command (slowdit/models.py) is
#
#     scripts/run_tests.sh --junitxml=/out/results.xml
#
# so this repo can run in its own sandbox: the smoke run in the README
# stages this repo as code and executes this script against the code in
# /code — the image only provides the Python environment (the executor
# sets PYTHONPATH=/code, so `import slowdit` resolves to the code under
# test, not to anything installed in the image).
set -e

junit="results.xml"
for arg in "$@"; do
    case "$arg" in
    --junitxml=*) junit="${arg#--junitxml=}" ;;
    esac
done

# The executor's workdir is the repo root; this keeps the contract working
# from any workdir.
cd "$(dirname "$0")/.."

# The sandbox image ships `python`; CI runners may only have `python3`.
if command -v python >/dev/null 2>&1; then py=python; else py=python3; fi

# -p no:cacheprovider: /code is read-only in the sandbox; don't even try
# to write .pytest_cache.
exec "$py" -m pytest -q -p no:cacheprovider --junitxml="$junit"
