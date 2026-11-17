#!/usr/bin/env bash
# Run test files the way CI does: one file per run, with the pinned test requirements.
#
#   harness/run_tests.sh [--slot --pkg <X> --item <item>] [--run-root <dir>] <test file>...
#
# Test paths are relative to the repository root. Each file runs whole with unittest, as the union
# of its CI shards does. The shard plan is not used, so a file that CI splits across shards
# (test_motion.py) or gives to another shard (test_cad_model.py) still runs all its tests.
#
# Every invocation makes a fresh run directory, <run root>/<run id>/, and never reuses one:
#   log   all test output, then one `PASS|FAIL <seconds>s <file>` line per file
#   pid   the script's pid, written at start
#   done  `rc=<code>`, written once on exit (also on failure and on SIGTERM/SIGINT/SIGHUP, rc=143)
# The first stdout line is `RUN_DIR=<path>`, so a watcher reads exactly that run's `done`.
# A pass is `rc=0` in that run's `done`. The script exits 0 only if every file passed.
# The run id is <timestamp>-<pid>-<random>; the run root is --run-root or $RUN_ROOT, else
# ${TMPDIR:-/tmp}/cad-agent-run_tests. An existing run directory is refused (exit 2).
#
# --slot (optional, dev only) runs inside ~/cad-bench/bin/suite-slot. It needs --pkg and --item
# (or $PKG and $ITEM), and the run root defaults to ~/cad-bench/pkg<X>/<item>/. The run directory
# is created and printed before the script waits for a slot.
set -u

usage() {
  echo "usage: $0 [--slot --pkg <X> --item <item>] [--run-root <dir>] <test file>..." >&2
  exit 2
}

slot=0
pkg=${PKG:-}
item=${ITEM:-}
run_root=${RUN_ROOT:-}
run_dir=
while [ "$#" -gt 0 ]; do
  case $1 in
    --slot) slot=1 ;;
    --pkg) [ "$#" -ge 2 ] || usage; pkg=$2; shift ;;
    --item) [ "$#" -ge 2 ] || usage; item=$2; shift ;;
    --run-root) [ "$#" -ge 2 ] || usage; run_root=$2; shift ;;
    --run-dir) [ "$#" -ge 2 ] || usage; run_dir=$2; shift ;; # internal: the --slot re-exec
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
  shift
done
[ "$#" -gt 0 ] || usage

if [ -z "$run_dir" ]; then
  if [ "$slot" -eq 1 ]; then
    [ -n "$pkg" ] || { echo "--slot needs --pkg or PKG" >&2; exit 2; }
    [ -n "$item" ] || { echo "--slot needs --item or ITEM" >&2; exit 2; }
    base=${run_root:-$HOME/cad-bench/pkg$pkg/$item}
  else
    base=${run_root:-${TMPDIR:-/tmp}/cad-agent-run_tests}
  fi
  mkdir -p "$base" || exit 2
  run_dir=$base/${RUN_ID:-$(date +%Y%m%dT%H%M%S)-$$-$RANDOM}
  # plain mkdir: it fails when the directory exists, so an old run's marker is never reused
  mkdir "$run_dir" 2> /dev/null || { echo "refusing to reuse run dir $run_dir" >&2; exit 2; }
elif [ ! -d "$run_dir" ] || [ -e "$run_dir/done" ] || [ -e "$run_dir/log" ]; then
  echo "refusing to reuse run dir $run_dir" >&2
  exit 2
fi
echo "RUN_DIR=$run_dir"
echo $$ > "$run_dir/pid"

child=
finish() {
  rc=$?
  echo "rc=$rc" > "$run_dir/done.tmp" && mv "$run_dir/done.tmp" "$run_dir/done"
}
stop() {
  [ -z "$child" ] || kill "$child" 2> /dev/null
}
trap finish EXIT
trap 'stop; exit 143' TERM INT HUP

if [ "$slot" -eq 1 ]; then
  exec "$HOME/cad-bench/bin/suite-slot" "$0" --run-dir "$run_dir" "$@"
fi

root=$(dirname "$0")/..
failed=0
for test in "$@"; do
  started=$SECONDS
  echo "== $test" >> "$run_dir/log"
  (cd "$root" && exec uv run --no-project --python 3.12 --with-requirements harness/test-requirements.txt \
    python -B -m unittest discover -s "$(dirname "$test")" -p "$(basename "$test")") \
    >> "$run_dir/log" 2>&1 < /dev/null &
  child=$!
  if wait "$child"; then
    result=PASS
  else
    result=FAIL
    failed=$((failed + 1))
  fi
  child=
  line="$result $((SECONDS - started))s $test"
  echo "$line"
  echo "$line" >> "$run_dir/log"
done
[ "$failed" -eq 0 ]
