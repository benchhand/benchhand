#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fail-fast evidence runs: which producer steps a scope runs, and running them.

`plan` is pure. It takes one column of the producer x iteration matrix (evidence.py MATRIX), the
steps that do not apply to this version or stage, the steps that have stale or open obligations
due at the stage, and the command of each step. It returns the steps in the fail-fast read order
(STEP_ORDER), each one either to run or skipped with its reason. A step marked R runs when it
applies. A step marked C runs only when something it covers is stale or open and due. Every other
mark (O, reads, -) is not part of the run. The full scope runs sequentially.

`execute` runs a plan. It takes its effects as arguments: `validate` runs the record validator in
process and returns (exit status, result); `spawn(step)` starts one producer and returns a
`Running` handle; `strays()` lists recorded runs that ended and left processes behind, or cannot
be verified. Validation runs first. A failed validation starts no producer. Before the first
producer starts, any stray ends the run with no result. Outside the full scope every producer
starts at once; their results are read in plan order, and at the first blocking result every
later producer that still runs is stopped (`Running.stop`, a process group terminate). In the
full scope each producer starts after the one before it passed. A producer that exits 0 passes, 1
fails, and any other status (2, a signal, a failure to start) is no result. Both block. Strays
found after the run also end it with no result.

Standard library only; load it by path as `cad_record_fail_fast` (dataclasses look it up in
sys.modules).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

SCOPES = ("inner", "commit", "provisional", "full")
# The fail-fast read order: the record validator, the form check, static interference, the smoke
# or full sweep, renders, the default motion render.
STEP_ORDER = ("validate", "form", "static.changed", "static.all", "sweep.smoke", "sweep.full", "render", "motion.default")
RUNS = frozenset({"R", "C"})  # matrix marks that put a step in a run
STATUSES = ("pass", "fail", "no_result", "skipped", "stopped", "carried")  # carried: reserved for a carried sweep
BLOCKING = frozenset({"fail", "no_result"})
EXIT = {"pass": 0, "fail": 1, "no_result": 2}
POLL_SECONDS = 0.05


@dataclass(frozen=True)
class Step:
    """One step of a plan: `argv` is None for the in-process validator; `skip` says why it does not run."""

    id: str
    argv: tuple[str, ...] | None = None
    skip: str | None = None


@dataclass(frozen=True)
class Plan:
    scope: str
    sequential: bool
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class StepResult:
    id: str
    argv: tuple[str, ...] | None
    exit: int | None
    status: str  # one of STATUSES
    result: Any
    seconds: float | None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "argv": list(self.argv) if self.argv is not None else None, "exit": self.exit,
                "status": self.status, "result": self.result,
                "seconds": round(self.seconds, 3) if self.seconds is not None else None}


@dataclass(frozen=True)
class Outcome:
    status: str  # pass, fail or no_result
    steps: tuple[StepResult, ...]
    stopped_at: str | None
    strays: tuple[Mapping[str, Any], ...] = ()

    @property
    def exit(self) -> int:
        return EXIT[self.status]


class Running(Protocol):
    """A started producer. `poll` is its exit status once it ended, else None. `stop` terminates
    what still runs of it, reaps it and returns its exit status; it is called once per producer,
    also after one that ended by itself. `result` is what the producer reported."""

    def poll(self) -> int | None: ...

    def stop(self) -> int: ...

    def result(self) -> Any: ...


def plan(scope: str, column: Mapping[str, str], argv: Mapping[str, Sequence[str]], *,
         not_applicable: Mapping[str, str] | None = None, stale: Collection[str] = ()) -> Plan:
    """The steps of `scope` from its matrix `column` (step id -> mark), in STEP_ORDER. `argv` holds
    the command of each producer step; `not_applicable` maps a step to why it does not apply to this
    version or stage; `stale` lists the steps that cover a stale or open obligation due at the stage."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; one of {', '.join(SCOPES)}")
    not_applicable = not_applicable or {}
    steps = []
    for ident in STEP_ORDER:
        mark = column.get(ident, "-")
        if mark not in RUNS:
            continue
        command = None if ident == "validate" else tuple(argv[ident])
        skip = not_applicable.get(ident)
        if skip is None and mark == "C" and ident not in stale:
            skip = "nothing it covers is stale or open and due at this stage"
        steps.append(Step(ident, command, skip))
    return Plan(scope, scope == "full", tuple(steps))


def status_of(code: int | None) -> str:
    """A producer's step status from its exit status: 0 pass, 1 fail, anything else no result."""
    return {0: "pass", 1: "fail"}.get(code, "no_result") if code is not None else "no_result"


def execute(work: Plan, *, validate: Callable[[], tuple[int, Any]], spawn: Callable[[Step], Running],
            strays: Callable[[], Sequence[Mapping[str, Any]]], clock: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep) -> Outcome:
    """Run `work` (see the module docstring)."""
    done: dict[str, StepResult] = {}
    for step in work.steps:
        if step.skip is not None:
            done[step.id] = StepResult(step.id, step.argv, None, "skipped", {"reason": step.skip}, None)
    producers = [step for step in work.steps if step.skip is None and step.argv is not None]
    stopped_at: str | None = None
    left: tuple[Mapping[str, Any], ...] = ()
    if any(step.id == "validate" and step.skip is None for step in work.steps):
        started = clock()
        code, result = validate()
        done["validate"] = StepResult("validate", None, code, status_of(code), result, clock() - started)
        if done["validate"].status in BLOCKING:
            stopped_at = "validate"
    if stopped_at is None and producers:
        left = tuple(strays())
        if not left:
            stopped_at = (_sequential if work.sequential else _parallel)(producers, spawn, done, clock, sleep)
            left = tuple(strays())
    for step in producers:
        done.setdefault(step.id, StepResult(step.id, step.argv, None, "stopped", None, None))
    steps = tuple(done[step.id] for step in work.steps)
    if left:
        return Outcome("no_result", steps, stopped_at, left)
    return Outcome(done[stopped_at].status if stopped_at is not None else "pass", steps, stopped_at)


def _start(step: Step, spawn: Callable[[Step], Running]) -> Running | str:
    try:
        return spawn(step)
    except (OSError, RuntimeError, ValueError) as error:
        return f"cannot start: {error}"


def _finished(step: Step, process: Running | str, code: int | None, seconds: float | None) -> StepResult:
    if isinstance(process, str):
        return StepResult(step.id, step.argv, None, "no_result", {"error": process}, 0.0)
    return StepResult(step.id, step.argv, code, status_of(code), process.result(), seconds)


def _wait(processes: Mapping[str, Running | str], ended: dict[str, float], wanted: str,
          clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Poll every producer still running until `wanted` has ended, noting when each one ends."""
    while True:
        for ident, process in processes.items():
            if ident not in ended and (isinstance(process, str) or process.poll() is not None):
                ended[ident] = clock()
        if wanted in ended:
            return
        sleep(POLL_SECONDS)


def _sequential(producers: Sequence[Step], spawn: Callable[[Step], Running], done: dict[str, StepResult],
                clock: Callable[[], float], sleep: Callable[[float], None]) -> str | None:
    for step in producers:
        started = clock()
        process = _start(step, spawn)
        ended: dict[str, float] = {}
        try:
            _wait({step.id: process}, ended, step.id, clock, sleep)
        finally:
            code = process.stop() if not isinstance(process, str) else None
        done[step.id] = _finished(step, process, code, ended.get(step.id, clock()) - started)
        if done[step.id].status in BLOCKING:
            return step.id
    return None


def _parallel(producers: Sequence[Step], spawn: Callable[[Step], Running], done: dict[str, StepResult],
              clock: Callable[[], float], sleep: Callable[[float], None]) -> str | None:
    started = clock()
    processes: dict[str, Running | str] = {}
    ended: dict[str, float] = {}
    stopped: set[str] = set()
    try:
        for step in producers:
            processes[step.id] = _start(step, spawn)
        for index, step in enumerate(producers):
            _wait(processes, ended, step.id, clock, sleep)
            process = processes[step.id]
            code = process.stop() if not isinstance(process, str) else None
            stopped.add(step.id)
            done[step.id] = _finished(step, process, code, ended[step.id] - started)
            if done[step.id].status not in BLOCKING:
                continue
            for later in producers[index + 1:]:
                other = processes[later.id]
                if isinstance(other, str):
                    continue
                if later.id in ended or other.poll() is not None:
                    code = other.stop()
                    done[later.id] = _finished(later, other, code, ended.get(later.id, clock()) - started)
                else:
                    done[later.id] = StepResult(later.id, later.argv, other.stop(), "stopped", None, clock() - started)
                stopped.add(later.id)
            return step.id
        return None
    finally:
        for ident, process in processes.items():
            if ident not in stopped and not isinstance(process, str):
                process.stop()
