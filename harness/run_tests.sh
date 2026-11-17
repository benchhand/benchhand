#!/usr/bin/env bash
# Run test files the way CI does: one file per run, with the pinned test requirements.
#
#   harness/run_tests.sh [--slot] <test file>...
#
# Test paths are relative to the repository root.
# Prints one `PASS|FAIL <seconds>s <file>` line per file and exits non-zero if any file fails.
# A file that scripts/ci_shards.py does not plan runs whole in the `rest` shard (no SHARDS set).
# --slot (optional, dev only) runs the whole script inside ~/cad-bench/bin/suite-slot.
# When MARKER is set, the script writes `rc=<code>` to it on exit; rc=0 means every file passed.
set -u

if [ "${1:-}" = "--slot" ]; then
  shift
  exec "$HOME/cad-bench/bin/suite-slot" "$0" "$@"
fi

if [ -n "${MARKER:-}" ]; then
  trap 'echo rc=$? > "$MARKER"' EXIT
  trap 'exit 143' TERM INT HUP
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 [--slot] <test file>..." >&2
  exit 2
fi

root=$(dirname "$0")/..
failed=0
for test in "$@"; do
  started=$SECONDS
  if (cd "$root" && uv run --no-project --python 3.12 --with-requirements harness/test-requirements.txt \
    python -B scripts/ci_shards.py run rest "$test" > /dev/null 2>&1 < /dev/null); then
    result=PASS
  else
    result=FAIL
    failed=$((failed + 1))
  fi
  echo "$result $((SECONDS - started))s $test"
done
[ "$failed" -eq 0 ]
