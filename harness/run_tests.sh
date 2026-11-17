#!/usr/bin/env bash
# Run test files the way CI does: one file per run, with the pinned test requirements.
#
#   harness/run_tests.sh [--slot --pkg <X> --item <item>] [--run-root <dir>] <test file>...
#
# Test paths are relative to the repository root. Each file runs whole with unittest, as the union
# of its CI shards does. The shard plan is not used, so a file that CI splits across shards
# (test_motion.py) or gives to another shard (test_cad_model.py) still runs all its tests.
# Files under harness/ and scripts/ run on harness/test-requirements-min.txt (PyYAML only), as in
# CI; every other file runs on harness/test-requirements.txt.
#
# Every invocation makes a fresh run directory, <run root>/<run id>/, and never reuses one:
#   log   all test output, then one `PASS|FAIL <seconds>s <file>` line per file
#   pid   the script's pid, written at start (kill it to cancel the run)
#   done  `rc=<code>`, written once when the script exits
# The first stdout line is `RUN_DIR=<path>`, so a watcher reads exactly that run's `done`.
# A pass is `rc=0` in that run's `done`. The script exits 0 only if every file passed.
# The run id is <timestamp>-<pid>-<random>; the run root is --run-root or $RUN_ROOT, else
# ${TMPDIR:-/tmp}/cad-agent-run_tests. An existing run directory is refused (exit 2), and then
# nothing in it is touched.
#
# `done` holds the script's exit code: 1 when a test file failed or a log/pid write failed,
# 143 after SIGTERM, SIGINT or SIGHUP (the running test is stopped first), or the exit code of
# suite-slot under --slot. No usage error writes one: those exit 2 before the directory exists.
# SIGKILL cannot be trapped, so a killed script leaves no `done`.
#
# --slot (optional, dev only) runs the tests under ~/cad-bench/bin/suite-slot. It needs --pkg and
# --item (or $PKG and $ITEM), and the run root defaults to ~/cad-bench/pkg<X>/<item>/. This script
# stays the parent: it prints the run directory, waits for suite-slot, forwards signals to it and
# writes `done` from suite-slot's exit code, so a slot that is killed while queued, or that exits
# without running the command, still ends in a `done`. suite-slot re-runs this script with the
# internal --run-dir flag; that worker claims the directory once (a `claimed` file) and never
# writes `done`.
set -euo pipefail

usage() {
  echo "usage: $0 [--slot --pkg <X> --item <item>] [--run-root <dir>] <test file>..." >&2
  exit 2
}

refuse() {
  echo "refusing to reuse run dir $1" >&2
  exit 2
}

slot=0
pkg=${PKG:-}
item=${ITEM:-}
run_root=${RUN_ROOT:-}
worker_dir=
while [ "$#" -gt 0 ]; do
  case $1 in
    --slot) slot=1 ;;
    --pkg) [ "$#" -ge 2 ] || usage; pkg=$2; shift ;;
    --item) [ "$#" -ge 2 ] || usage; item=$2; shift ;;
    --run-root) [ "$#" -ge 2 ] || usage; run_root=$2; shift ;;
    --run-dir) [ "$#" -ge 2 ] || usage; worker_dir=$2; shift ;; # internal: the suite-slot re-run
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
  shift
done
[ "$#" -gt 0 ] || usage

child=
stop_child() {
  [ -n "$child" ] || return 0
  kill "$child" 2> /dev/null || return 0
  for _ in $(seq 50); do
    kill -0 "$child" 2> /dev/null || break
    sleep 0.1
  done
  kill -9 "$child" 2> /dev/null || true
  wait "$child" 2> /dev/null || true
}

run_files() {
  local root test started result line requirements
  root=$(dirname "$0")/..
  local failed=0
  for test in "$@"; do
    started=$SECONDS
    case $test in
      harness/* | scripts/*) requirements=harness/test-requirements-min.txt ;;
      *) requirements=harness/test-requirements.txt ;;
    esac
    echo "== $test" >> "$run_dir/log"
    (cd "$root" && exec uv run --no-project --python 3.12 --with-requirements "$requirements" \
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
}

if [ -n "$worker_dir" ]; then
  # the suite-slot re-run: the parent owns pid and done, this process only runs the files once
  run_dir=$worker_dir
  [ -d "$run_dir" ] && [ ! -e "$run_dir/done" ] || refuse "$run_dir"
  # noclobber makes the redirect an exclusive create: a second handoff to this directory fails
  (set -o noclobber; echo $$ > "$run_dir/claimed") 2> /dev/null || refuse "$run_dir"
  trap 'stop_child; exit 143' TERM INT HUP
  run_files "$@"
  exit
fi

if [ "$slot" -eq 1 ]; then
  [ -n "$pkg" ] || { echo "--slot needs --pkg or PKG" >&2; exit 2; }
  [ -n "$item" ] || { echo "--slot needs --item or ITEM" >&2; exit 2; }
  base=${run_root:-$HOME/cad-bench/pkg$pkg/$item}
else
  base=${run_root:-${TMPDIR:-/tmp}/cad-agent-run_tests}
fi
mkdir -p "$base"
run_dir=$base/${RUN_ID:-$(date +%Y%m%dT%H%M%S)-$$-$RANDOM}
# plain mkdir: it fails when the directory exists, so an old run's marker is never reused
mkdir "$run_dir" 2> /dev/null || refuse "$run_dir"

# Traps come before any write into the directory, so a failed write still ends in a `done`.
finish() {
  local rc=$?
  set +e
  trap - EXIT
  echo "rc=$rc" > "$run_dir/done.tmp" && mv "$run_dir/done.tmp" "$run_dir/done"
  exit "$rc"
}
trap finish EXIT
trap 'stop_child; exit 143' TERM INT HUP

echo "RUN_DIR=$run_dir"
echo $$ > "$run_dir/pid"

if [ "$slot" -eq 1 ]; then
  "$HOME/cad-bench/bin/suite-slot" "$0" --run-dir "$run_dir" "$@" &
  child=$!
  rc=0
  wait "$child" || rc=$?
  child=
  exit "$rc"
fi
run_files "$@"
