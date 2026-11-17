#!/usr/bin/env bash
# Run test files the way CI does: one file per run, with the pinned test requirements.
#
#   harness/run_tests.sh [--slot --pkg <X> --item <item>] [--run-root <dir>] [--expect-head <sha>] <test file>...
#
# Before anything else it checks that the repository root is a git work tree (a copy synced without
# .git is refused, exit 2). With --expect-head <sha> (a full SHA or a prefix of 7+ characters) it
# also checks that HEAD starts with it (else exit 2, both SHAs printed). Uncommitted changes to
# tracked files only warn. Under --slot the parent checks before it waits for a slot.
#
# Test paths may be relative to the repository root (`scripts/x.py`, `./scripts/x.py`) or absolute
# inside it; each is normalized to a repo-relative path first. Each file runs whole with unittest,
# as the union of its CI shards does. The shard plan is not used, so a file that CI splits across
# shards (test_motion.py) or gives to another shard (test_cad_model.py) still runs all its tests.
# Files under harness/ and scripts/ run on harness/test-requirements-min.txt (PyYAML only), as in
# CI; every other file runs on harness/test-requirements.txt.
#
# Every invocation makes one fresh run directory, <run root>/<run id>/, and never reuses one:
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
# 143 after SIGTERM, SIGINT or SIGHUP, or the exit code of suite-slot under --slot. No usage error
# writes one: those exit 2 before the directory exists.
# `done` is written only after no process of the running test, or of suite-slot, is left: each runs
# in its own process group (via perl or setsid), and a signal sends TERM to the whole group, waits
# RUN_TESTS_GRACE seconds (default 10), then sends KILL to the group.
# SIGKILL cannot be trapped, so a killed script leaves no `done`.
#
# --slot (optional, dev only) runs the tests under ~/cad-bench/bin/suite-slot. It needs --pkg and
# --item (or $PKG and $ITEM), and the run root defaults to ~/cad-bench/pkg<X>/<item>/. This script
# stays the parent: it prints the run directory, waits for suite-slot, signals its group and
# writes `done` from suite-slot's exit code, so a slot that is killed while queued, or that exits
# without running the command, still ends in a `done`. suite-slot re-runs this script with the
# internal --run-dir flag; that worker claims the directory once (a `claimed` file) and never
# writes `done`.
set -euo pipefail

usage() {
  echo "usage: $0 [--slot --pkg <X> --item <item>] [--run-root <dir>] [--expect-head <sha>] <test file>..." >&2
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
expect_head=
while [ "$#" -gt 0 ]; do
  case $1 in
    --slot) slot=1 ;;
    --pkg) [ "$#" -ge 2 ] || usage; pkg=$2; shift ;;
    --item) [ "$#" -ge 2 ] || usage; item=$2; shift ;;
    --run-root) [ "$#" -ge 2 ] || usage; run_root=$2; shift ;;
    --expect-head) [ "$#" -ge 2 ] || usage; expect_head=$2; shift ;;
    --run-dir) [ "$#" -ge 2 ] || usage; worker_dir=$2; shift ;; # internal: the suite-slot re-run
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
  shift
done
[ "$#" -gt 0 ] || usage
# a full SHA or a prefix of at least 7 hex characters
case $expect_head in
  *[!0-9a-fA-F]*) usage ;;
  '') ;;
  ???????*) ;;
  *) usage ;;
esac

# Every test command, and the suite-slot child, runs in its own process group (own_group=1), so a
# signal reaches its grandchildren too. The suite-slot worker (--run-dir) leaves its children in the
# slot's group instead: the parent signals that whole group and owns the cleanup.
own_group=1
[ -z "$worker_dir" ] || own_group=0
grace=${RUN_TESTS_GRACE:-10}

pg_launch() {
  if command -v perl > /dev/null 2>&1; then
    exec perl -e 'setpgrp(0, 0); exec @ARGV or die "exec $ARGV[0]: $!"' -- "$@"
  elif command -v setsid > /dev/null 2>&1; then
    exec setsid "$@"
  fi
  echo "run_tests.sh needs perl or setsid to run tests in a process group" >&2
  exit 127
}

# true while a process of group $1 other than a zombie is left
group_alive() {
  ps -A -o pgid= -o stat= 2> /dev/null | awk -v g="$1" '$1 == g && $2 !~ /^Z/ { found = 1 } END { exit !found }'
}

signal_group() {
  if [ "$own_group" -eq 1 ]; then
    kill -s "$1" -- "-$child" 2> /dev/null || kill -s "$1" "$child" 2> /dev/null || true
  else
    kill -s "$1" "$child" 2> /dev/null || true
  fi
}

wait_group_gone() {
  local ticks=$(($1 * 10))
  while [ "$ticks" -gt 0 ] && group_alive "$child"; do
    sleep 0.1
    ticks=$((ticks - 1))
  done
  ! group_alive "$child"
}

child=
# TERM the child's whole group, give it the grace period, then KILL it; return once none is left.
stop_child() {
  [ -n "$child" ] || return 0
  if [ "$own_group" -eq 1 ]; then
    signal_group TERM
    wait_group_gone "$grace" || { signal_group KILL; wait_group_gone 5 || true; }
    wait "$child" 2> /dev/null || true
  else
    signal_group TERM
  fi
  child=
}

# After the child exited by itself: kill what its tests left running in the group.
sweep_group() {
  if [ "$own_group" -eq 1 ] && group_alive "$child"; then
    signal_group KILL
    wait_group_gone 5 || true
  fi
}

root_logical=$(cd "$(dirname "$0")/.." && pwd)
root_real=$(cd "$(dirname "$0")/.." && pwd -P)

# The test path relative to the repository root: the root prefix, ./ and // are removed.
repo_relative() {
  local path=$1 prefix
  case $path in /*) ;; *) path=./$path ;; esac
  for prefix in "$root_real" "$root_logical"; do
    case $path in "$prefix"/*) path=${path#"$prefix"/} ;; esac
  done
  while :; do
    case $path in
      ./*) path=${path#./} ;;
      /*) path=${path#/} ;;
      *//*) path=${path%%//*}/${path#*//} ;;
      */./*) path=${path%%/./*}/${path#*/./} ;;
      *) break ;;
    esac
  done
  printf '%s\n' "$path"
}

exec_test() {
  cd "$root_logical" || exit 2
  if [ "$own_group" -eq 1 ]; then
    pg_launch uv run --no-project --python 3.12 --with-requirements "$1" python -B -m unittest discover -s "$2" -p "$3"
  fi
  exec uv run --no-project --python 3.12 --with-requirements "$1" python -B -m unittest discover -s "$2" -p "$3"
}

run_files() {
  local test relative started result line requirements
  local failed=0
  for test in "$@"; do
    started=$SECONDS
    relative=$(repo_relative "$test")
    case $relative in
      harness/* | scripts/*) requirements=harness/test-requirements-min.txt ;;
      *) requirements=harness/test-requirements.txt ;;
    esac
    echo "== $relative" >> "$run_dir/log"
    (exec_test "$requirements" "$(dirname "$relative")" "$(basename "$relative")") \
      >> "$run_dir/log" 2>&1 < /dev/null &
    child=$!
    if wait "$child"; then
      result=PASS
    else
      result=FAIL
      failed=$((failed + 1))
    fi
    sweep_group
    child=
    line="$result $((SECONDS - started))s $relative"
    echo "$line"
    echo "$line" >> "$run_dir/log"
  done
  [ "$failed" -eq 0 ]
}

check_checkout() {
  local head want
  if [ "$(git -C "$root_logical" rev-parse --is-inside-work-tree 2> /dev/null)" != true ]; then
    echo "run_tests: $root_logical is not a git work tree (a synced copy without .git?)" >&2
    exit 2
  fi
  head=$(git -C "$root_logical" rev-parse HEAD)
  if [ -n "$expect_head" ]; then
    want=$(printf '%s' "$expect_head" | tr 'A-F' 'a-f')
    case $head in
      "$want"*) ;;
      *) echo "run_tests: HEAD is $head, expected $expect_head" >&2; exit 2 ;;
    esac
  fi
  if ! git -C "$root_logical" diff --quiet HEAD -- 2> /dev/null; then
    echo "run_tests: warning: uncommitted changes to tracked files in $root_logical" >&2
  fi
}
[ -n "$worker_dir" ] || check_checkout

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
  stop_child
  echo "rc=$rc" > "$run_dir/done.tmp" && mv "$run_dir/done.tmp" "$run_dir/done"
  exit "$rc"
}
trap finish EXIT
trap 'stop_child; exit 143' TERM INT HUP

echo "RUN_DIR=$run_dir"
echo $$ > "$run_dir/pid"

if [ "$slot" -eq 1 ]; then
  pg_launch "$HOME/cad-bench/bin/suite-slot" "$0" --run-dir "$run_dir" "$@" &
  child=$!
  rc=0
  wait "$child" || rc=$?
  sweep_group
  child=
  exit "$rc"
fi
run_files "$@"
