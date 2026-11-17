#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Work ledger of the cad-flow lead: units, dependencies, claims, leases, evidence comments and human gates.

Usage:
    uv run --script work.py [--project DIR] init
    uv run --script work.py [--project DIR] graph  --file GRAPH.json
    uv run --script work.py [--project DIR] units | blocked | stale | gates
    uv run --script work.py [--project DIR] ready
    uv run --script work.py [--project DIR] claim UNIT [--actor ACTOR]
    uv run --script work.py [--project DIR] heartbeat UNIT [--actor ACTOR]
    uv run --script work.py [--project DIR] unclaim UNIT --holder ACTOR [--reason TEXT]
    uv run --script work.py [--project DIR] comment UNIT TEXT [--actor ACTOR]
    uv run --script work.py [--project DIR] evidence UNIT PATH [--actor ACTOR]
    uv run --script work.py [--project DIR] comments UNIT
    uv run --script work.py [--project DIR] close UNIT [--reason TEXT] [--actor ACTOR]
    uv run --script work.py [--project DIR] reopen UNIT [--reason TEXT]
    uv run --script work.py [--project DIR] gate-create NAME --blocks UNIT [UNIT ...] [--reason TEXT]
    uv run --script work.py [--project DIR] gate-waiter NAME WAITER
    uv run --script work.py [--project DIR] gate-void OLD NEW [--reopen UNIT ...] [--reason TEXT]
    uv run --script work.py [--project DIR] findings

GRAPH.json is a list of {"unit": U, "stage": S, "title": T, "deps": [U, ...]}; `graph` creates what is
missing and never duplicates. Every command prints one JSON document; a refusal prints {"error": ...}
on stderr and exits 1.

The ledger lives in Beads (bd >= 1.3.1) when <project>/.beads exists, otherwise in the file ledger
<project>/hw/.cad/work/ledger.json with the lease of each unit in <project>/.cad-work/<UNIT>/status.json.
Both expose the same operations and the same lease fields.

Gates are mirrors: flow.py decides, work.py records. The CLI has no way to resolve a gate; only
`resolve_gate`, which flow.py calls with an accepted decision, closes one.

`ready` and `claim` take the accepted-gate set from flow.gates in <project>/hw/product.yaml (read only; an
absent file or key means nothing is approved yet). flow.gates maps a gated stage to
{state: provisional|approved, version, provisional, approved}; any other shape is an error. A gate closed in the
ledger that flow.gates does not bind keeps the units behind it blocked, and `findings` reports it as
`ledger.gate_unbound`, as it reports the reverse. The cad-record YAML loader, which needs PyYAML, reads the file.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Sequence

MIN_BD = (1, 3, 1)  # 1.3.0 added leases and assignee-checked updates; 1.3.1 fixed `bd ready` fan-in
LEASE_TTL = timedelta(minutes=5)  # bd 1.3.1 grants 5-minute leases and has no setting; the file backend matches
BUSY_TIMEOUT_S = 30.0
BD_TIMEOUT_S = 60.0  # one bd command; a store that does not answer by then is reported busy or unreachable
BD_INIT_TIMEOUT_S = 300.0  # the one exception: creating a store takes longer; it also ends in BusyError
BACKOFF_START_S = 0.05
BACKOFF_CAP_S = 2.0
BUSY_PATTERN = re.compile(r"locked|\bbusy\b|lock (?:is )?held|could not acquire|try again|timed out waiting", re.I)
OPENCODE_LOCK = "omp-embedded-write.lock"  # the OpenCode Beads plugin's write lock, shared when present
OWN_LOCK = "cad-work.lock"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
UNIT_LABEL, STAGE_LABEL, GATE_LABEL, VOID_LABEL = ("cad:unit=", "cad:stage=", "cad:gate=", "cad:void")
GATE_UNBOUND = "ledger.gate_unbound"
FLOW_ACTOR = "cad/flow"
DECISION_ID = re.compile(r"DR-[0-9]{3}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class WorkError(Exception):
    """The ledger refused an operation, or its store failed."""


class ClaimError(WorkError):
    """A claim, heartbeat, release or close was refused because of who holds the unit."""


class BusyError(WorkError):
    """The store stayed busy for the whole retry budget."""


@dataclass(frozen=True)
class UnitSpec:
    unit: str
    stage: str
    title: str = ""
    deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unit:
    unit: str
    stage: str
    id: str
    status: str  # open | in_progress | closed
    assignee: str | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    deps: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()  # open gates that block it
    blocked_by: tuple[str, ...] = ()  # filled by `blocked`: unfinished units and gates


@dataclass(frozen=True)
class Gate:
    name: str
    id: str
    status: str  # open | closed
    void: bool = False
    blocks: tuple[str, ...] = ()
    waiters: tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptedDecision:
    """What flow.py holds once it accepted a decision record; the only key to `resolve_gate`."""

    dr_id: str
    version: int
    evidence_sha256: str
    status: str = "accepted"

    def __post_init__(self) -> None:
        if self.status != "accepted":
            raise ValueError(f"a gate needs an accepted decision, not {self.status!r}")
        if not DECISION_ID.fullmatch(self.dr_id):
            raise ValueError(f"not a decision record id: {self.dr_id!r}")
        if self.version < 1 or not SHA256.fullmatch(self.evidence_sha256):
            raise ValueError("a decision needs a version >= 1 and a sha256 evidence digest")

    @property
    def note(self) -> str:
        return f"{self.dr_id} v{self.version} evidence_sha256={self.evidence_sha256}"


def actor_of(unit: str) -> str:
    return f"cad/{unit}"


def _check_name(kind: str, value: str) -> str:
    if not NAME.fullmatch(value):
        raise WorkError(f"invalid {kind} name {value!r}: use letters, digits, '.', '_' or '-'")
    return value


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# --- busy store: lock plus backoff --------------------------------------------------------------------------------


class Backoff:
    """Exponential waits that together never pass `timeout`; sleep and clock are injectable for tests."""

    def __init__(
        self,
        timeout: float = BUSY_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sleep, self.monotonic = sleep, monotonic
        self.deadline = monotonic() + timeout
        self.delay = BACKOFF_START_S

    def wait(self) -> bool:
        """Sleep before the next attempt; False when the budget is spent."""
        left = self.deadline - self.monotonic()
        if left <= 0:
            return False
        self.sleep(min(self.delay, left))
        self.delay = min(self.delay * 2, BACKOFF_CAP_S)
        return True


@contextlib.contextmanager
def store_lock(path: Path, make_backoff: Callable[[], Backoff]) -> Iterator[None]:
    """Hold an exclusive flock on `path`, retrying with backoff while another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backoff = make_backoff()
    with path.open("a") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not backoff.wait():
                    raise BusyError(f"{path} stayed locked for {BUSY_TIMEOUT_S:.0f} s") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# --- backend interface --------------------------------------------------------------------------------------------


def check_successor(old: Gate, new: str, successor: Gate | None, stage_of: Mapping[str, str]) -> None:
    """Refuse a VOID successor that cannot take over.

    It must be a new name, or an open gate that blocks no unit from a stage that `old` does not block: such a gate
    belongs to another part of the flow, and moving the units onto it would hide them behind the wrong approval.
    """
    if new == old.name:
        raise WorkError("the successor gate needs its own name")
    if successor is None:
        return
    if successor.status == "closed" or successor.void:
        raise WorkError(
            f"gate {new} already exists and is {'void' if successor.void else 'closed'}; name a new successor"
        )
    foreign = {stage_of[u] for u in successor.blocks if u in stage_of} - {
        stage_of[u] for u in old.blocks if u in stage_of
    }
    if foreign:
        raise WorkError(
            f"gate {new} already blocks units of stage {', '.join(sorted(foreign))} that gate {old.name} does not; "
            "name a new successor"
        )


class Backend(ABC):
    """One work store. Names are unit and gate names; ids stay inside the backend."""

    @abstractmethod
    def add_units(self, specs: Sequence[UnitSpec]) -> list[str]:
        """Create the missing units and dependencies; return the names of the units created."""

    @abstractmethod
    def units(self) -> list[Unit]: ...

    @abstractmethod
    def unit(self, unit: str) -> Unit:
        """One unit's status and lease; `deps` and `gates` are only filled by `units`."""

    @abstractmethod
    def gate(self, name: str) -> Gate | None:
        """One gate's status and void flag; `blocks` and `waiters` are only filled by `gates`."""

    @abstractmethod
    def ready(self) -> list[Unit]:
        """Open units whose unit dependencies are closed and that no open gate blocks."""

    @abstractmethod
    def blocked(self) -> list[Unit]: ...

    @abstractmethod
    def claim(self, unit: str, actor: str) -> Unit: ...

    @abstractmethod
    def heartbeat(self, unit: str, actor: str) -> Unit: ...

    @abstractmethod
    def unclaim(self, unit: str, holder: str, reason: str) -> None: ...

    @abstractmethod
    def comment(self, unit: str, text: str, author: str) -> None: ...

    @abstractmethod
    def comments(self, unit: str) -> list[str]: ...

    @abstractmethod
    def close(self, unit: str, reason: str, actor: str) -> None: ...

    @abstractmethod
    def reopen(self, unit: str, reason: str) -> None:
        """Make a closed unit open and unclaimed."""

    @abstractmethod
    def gates(self) -> list[Gate]: ...

    @abstractmethod
    def gate_create(self, name: str, blocks: Sequence[str], reason: str) -> bool:
        """Create the human gate `name` blocking `blocks`; False when it already exists."""

    @abstractmethod
    def gate_block(self, name: str, unit: str) -> None: ...

    @abstractmethod
    def gate_add_waiter(self, name: str, waiter: str) -> None: ...

    @abstractmethod
    def gate_comment(self, name: str, text: str) -> None: ...

    @abstractmethod
    def gate_comments(self, name: str) -> list[str]: ...

    @abstractmethod
    def gate_void(self, old: str, new: str, reason: str) -> None:
        """Void `old` behind successor `new`, after `check_successor` accepts `new`.

        `new` is created on the units `old` blocks, or, when it exists and is open, takes those units on; it gets
        `old`'s waiters and a "supersedes" comment; only then is `old` marked void and closed. Every step skips what
        is already done, so a rerun after a crash finishes the job and repeats nothing. The file backend does all of
        it in one write."""

    @abstractmethod
    def _gate_close(self, name: str, reason: str) -> None:
        """Close a gate. Private: `resolve_gate` and `Ledger.void_gate` are the only callers."""


# --- bd backend ---------------------------------------------------------------------------------------------------


def _bd_process(bd: str, argv: Sequence[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one bd command with stdin closed. No answer within `timeout` is a BusyError, never a hang."""
    try:
        return subprocess.run(
            [bd, *argv], cwd=cwd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise BusyError(
            f"bd {argv[0]} did not answer within {timeout:.0f} s: the store is busy or unreachable"
        ) from None
    except OSError as error:
        raise WorkError(f"could not run bd: {error}") from error


def bd_version(bd: str = "bd", timeout: float = BD_TIMEOUT_S) -> tuple[int, ...] | None:
    """The installed bd version, or None when bd is absent or unreadable; a bd that never answers is a BusyError."""
    try:
        done = _bd_process(bd, ["--version"], None, timeout)
    except BusyError:
        raise
    except WorkError:
        return None
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", done.stdout)
    return tuple(int(part) for part in found.groups()) if found else None


class BdBackend(Backend):
    def __init__(
        self,
        project: Path,
        bd: str = "bd",
        make_backoff: Callable[[], Backoff] = Backoff,
        timeout: float = BD_TIMEOUT_S,
    ) -> None:
        self.project, self.bd, self.make_backoff = project, bd, make_backoff
        self.timeout = timeout
        beads = project / ".beads"
        shared = beads / OPENCODE_LOCK
        self.lock_path = shared if shared.exists() else beads / OWN_LOCK

    def _run(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run one bd command under the store lock, again with backoff while bd reports a busy store."""
        backoff = self.make_backoff()
        while True:
            with store_lock(self.lock_path, self.make_backoff):
                done = _bd_process(self.bd, argv, self.project, self.timeout)
            if done.returncode == 0 or not BUSY_PATTERN.search(done.stderr + done.stdout):
                return done
            if not backoff.wait():
                raise BusyError(f"bd {argv[0]} found the store busy for {BUSY_TIMEOUT_S:.0f} s")

    @staticmethod
    def _failure(done: subprocess.CompletedProcess[str]) -> str:
        """The line saying why bd failed; bd 1.3 prints some errors as a JSON object on stdout."""
        try:
            message = json.loads(done.stdout).get("error")
        except (ValueError, AttributeError):
            message = None
        text = (message or done.stderr or done.stdout).strip()
        return text.splitlines()[0] if text else f"bd exited {done.returncode}"

    def _bd(self, *argv: str, error: type[WorkError] = WorkError) -> Any:
        done = self._run(argv)
        if done.returncode != 0:
            raise error(f"bd {argv[0]}: {self._failure(done)}")
        try:
            return json.loads(done.stdout) if done.stdout.strip().startswith(("[", "{")) else None
        except ValueError as problem:
            raise WorkError(f"bd {argv[0]} printed invalid JSON: {problem}") from problem

    def _issues(self, *argv: str) -> list[dict[str, Any]]:
        found = self._bd(*argv, "--json")
        return found if isinstance(found, list) else []

    @staticmethod
    def _label(issue: dict[str, Any], prefix: str) -> str | None:
        return next((label[len(prefix) :] for label in issue.get("labels", ()) if label.startswith(prefix)), None)

    def _snapshot(self) -> tuple[list[Unit], list[Gate]]:
        listed = self._issues("list", "--label", "cad", "--all", "--limit", "0")
        unit_issues = [issue for issue in listed if self._label(issue, UNIT_LABEL)]
        gate_issues = [
            issue
            for issue in self._issues("list", "--type", "gate", "--all", "--limit", "0")
            if self._label(issue, GATE_LABEL)
        ]
        ids = [issue["id"] for issue in (*unit_issues, *gate_issues)]
        shown = {issue["id"]: issue for issue in self._issues("show", *ids)} if ids else {}
        unit_names = {issue["id"]: self._label(issue, UNIT_LABEL) for issue in unit_issues}
        gate_names = {issue["id"]: self._label(issue, GATE_LABEL) for issue in gate_issues}
        blocks: dict[str, list[str]] = {gate_id: [] for gate_id in gate_names}
        units = []
        for issue in unit_issues:
            full = shown.get(issue["id"], issue)
            deps, gates = [], []
            for dependency in full.get("dependencies", ()):
                if dependency["id"] in unit_names:
                    deps.append(unit_names[dependency["id"]])
                elif dependency["id"] in gate_names:
                    blocks[dependency["id"]].append(unit_names[issue["id"]])
                    if dependency.get("status") != "closed" and VOID_LABEL not in dependency.get("labels", ()):
                        gates.append(gate_names[dependency["id"]])
            units.append(
                Unit(
                    unit_names[issue["id"]],
                    self._label(issue, STAGE_LABEL) or "",
                    issue["id"],
                    issue["status"],
                    issue.get("assignee"),
                    issue.get("heartbeat_at"),
                    issue.get("lease_expires_at"),
                    tuple(deps),
                    tuple(gates),
                )
            )
        gates_out = [
            Gate(
                gate_names[issue["id"]],
                issue["id"],
                issue["status"],
                VOID_LABEL in issue.get("labels", ()),
                tuple(blocks[issue["id"]]),
                tuple(shown.get(issue["id"], issue).get("waiters", ())),
            )
            for issue in gate_issues
        ]
        return units, gates_out

    def units(self) -> list[Unit]:
        return self._snapshot()[0]

    def gates(self) -> list[Gate]:
        return self._snapshot()[1]

    @classmethod
    def _unit_of(cls, issue: dict[str, Any]) -> Unit:
        return Unit(
            cls._label(issue, UNIT_LABEL) or "",
            cls._label(issue, STAGE_LABEL) or "",
            issue["id"],
            issue["status"],
            issue.get("assignee"),
            issue.get("heartbeat_at"),
            issue.get("lease_expires_at"),
        )

    def _unit(self, unit: str) -> Unit:
        found = self._issues("list", "--label", f"{UNIT_LABEL}{unit}", "--all", "--limit", "0")
        if not found:
            raise WorkError(f"no unit {unit!r} in the ledger")
        return self._unit_of(found[0])

    def unit(self, unit: str) -> Unit:
        return self._unit(unit)

    def add_units(self, specs: Sequence[UnitSpec]) -> list[str]:
        existing = {item.unit: item for item in self.units()}
        created = []
        ids = {name: item.id for name, item in existing.items()}
        for spec in specs:
            if spec.unit in existing:
                continue
            made = self._bd(
                "create",
                spec.title or spec.unit,
                "-t",
                "task",
                "-l",
                f"cad,{STAGE_LABEL}{spec.stage},{UNIT_LABEL}{spec.unit}",
                "--json",
            )
            ids[spec.unit] = made["id"]
            created.append(spec.unit)
        for spec in specs:
            have = set(existing[spec.unit].deps) if spec.unit in existing else set()
            for dep in spec.deps:
                if dep not in ids:
                    raise WorkError(f"unit {spec.unit!r} depends on unknown unit {dep!r}")
                if dep not in have:
                    self._bd("dep", "add", ids[spec.unit], ids[dep], "--json")
        return created

    def ready(self) -> list[Unit]:
        issues = self._issues("ready", "--label", "cad", "--limit", "0")
        return [self._unit_of(issue) for issue in issues if self._label(issue, UNIT_LABEL)]

    def blocked(self) -> list[Unit]:
        issues = [issue for issue in self._issues("blocked") if self._label(issue, UNIT_LABEL)]
        if not issues:
            return []
        names = {
            issue["id"]: issue_name
            for issue in self._issues("list", "--label", "cad", "--all", "--limit", "0")
            if (issue_name := self._label(issue, UNIT_LABEL))
        }
        names.update(
            {
                issue["id"]: f"gate:{issue_name}"
                for issue in self._issues("list", "--type", "gate", "--all", "--limit", "0")
                if (issue_name := self._label(issue, GATE_LABEL))
            }
        )
        return [
            Unit(
                **{
                    **asdict(self._unit_of(issue)),
                    "blocked_by": tuple(names.get(b, b) for b in issue.get("blocked_by", ())),
                }
            )
            for issue in issues
        ]

    def claim(self, unit: str, actor: str) -> Unit:
        claimed = self._bd("update", self._unit(unit).id, "--claim", "--actor", actor, "--json", error=ClaimError)
        return self._unit_of(claimed[0]) if claimed else self._unit(unit)

    def heartbeat(self, unit: str, actor: str) -> Unit:
        self._bd("heartbeat", self._unit(unit).id, "--actor", actor, "--json", error=ClaimError)
        return self._unit(unit)

    def unclaim(self, unit: str, holder: str, reason: str) -> None:
        self._bd(
            "unclaim", self._unit(unit).id, "--if-assignee", holder, "--reason", reason, "--json", error=ClaimError
        )

    def comment(self, unit: str, text: str, author: str) -> None:
        self._bd("comments", "add", self._unit(unit).id, text, "--actor", author, "--json")

    def comments(self, unit: str) -> list[str]:
        found = self._bd("comments", self._unit(unit).id, "--json")
        return [item["text"] for item in found or ()]

    def close(self, unit: str, reason: str, actor: str) -> None:
        self._bd("close", self._unit(unit).id, "--reason", reason, "--actor", actor, "--json", error=ClaimError)

    def reopen(self, unit: str, reason: str) -> None:
        self._bd("reopen", self._unit(unit).id, "--reason", reason, "--json")
        # bd keeps the old assignee on a reopened issue. Read it back instead of trusting the reopen output, so a
        # retry after a failed release still finishes the job; a live claim (in_progress) is never released.
        current = self._unit(unit)
        if current.status == "open" and current.assignee:
            self.unclaim(unit, current.assignee, f"reopened: {reason}")

    def gate(self, name: str) -> Gate | None:
        found = self._issues("list", "--type", "gate", "--label", f"{GATE_LABEL}{name}", "--all", "--limit", "0")
        if not found:
            return None
        issue = found[0]
        return Gate(name, issue["id"], issue["status"], VOID_LABEL in issue.get("labels", ()))

    def _gate(self, name: str) -> Gate:
        found = self.gate(name)
        if found is None:
            raise WorkError(f"no gate {name!r} in the ledger")
        return found

    def gate_create(self, name: str, blocks: Sequence[str], reason: str) -> bool:
        if self.gate(name) is not None:
            return False
        targets = [self._unit(unit) for unit in blocks]
        made = self._bd(
            "gate",
            "create",
            "--type=human",
            "--blocks",
            targets[0].id,
            "--reason",
            reason,
            "--title",
            f"gate:{name}",
            "--json",
        )
        self._bd("label", "add", made["id"], f"{GATE_LABEL}{name}", "--json")
        for target in targets[1:]:
            self._bd("dep", "add", target.id, made["id"], "--json")
        return True

    def gate_block(self, name: str, unit: str) -> None:
        self._bd("dep", "add", self._unit(unit).id, self._gate(name).id, "--json")

    def gate_add_waiter(self, name: str, waiter: str) -> None:
        self._bd("gate", "add-waiter", self._gate(name).id, waiter)

    def gate_comment(self, name: str, text: str) -> None:
        self._bd("comments", "add", self._gate(name).id, text, "--actor", FLOW_ACTOR, "--json")

    def gate_comments(self, name: str) -> list[str]:
        return [item["text"] for item in self._bd("comments", self._gate(name).id, "--json") or ()]

    def gate_void(self, old: str, new: str, reason: str) -> None:
        units, gates = self._snapshot()
        known = {gate.name: gate for gate in gates}
        if old not in known:
            raise WorkError(f"no gate {old!r} in the ledger")
        previous, successor, note = known[old], known.get(new), f"supersedes {old}: {reason}"
        check_successor(previous, new, successor, {item.unit: item.stage for item in units})
        if not previous.blocks:
            raise WorkError(f"gate {old} blocks no unit, so there is nothing to move to a successor")
        # The successor holds the units and the waiters before the old gate is retired: a crash leaves `old` as it was.
        if successor is None:
            self.gate_create(new, previous.blocks, note)
        else:
            for unit in previous.blocks:
                if unit not in successor.blocks:
                    self.gate_block(new, unit)
        for waiter in previous.waiters:
            if successor is None or waiter not in successor.waiters:
                self.gate_add_waiter(new, waiter)
        if note not in self.gate_comments(new):
            self.gate_comment(new, note)
        if not previous.void:
            self._bd("label", "add", previous.id, VOID_LABEL, "--json")
        if previous.status == "open":
            self._gate_close(old, f"void: superseded by {new}")

    def _gate_close(self, name: str, reason: str) -> None:
        self._bd("gate", "resolve", self._gate(name).id, "--reason", reason)


def bd_init(project: Path, bd: str = "bd", timeout: float = BD_INIT_TIMEOUT_S) -> None:
    """Create the store: no git hooks (`core.hooksPath` is never set) and no agent instruction files."""
    done = _bd_process(bd, ["init", "--skip-hooks", "--skip-agents", "--quiet"], project, timeout)
    if done.returncode != 0:
        raise WorkError(f"bd init: {(done.stderr or done.stdout).strip() or done.returncode}")


# --- file backend -------------------------------------------------------------------------------------------------


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class FileBackend(Backend):
    """hw/.cad/work/ledger.json holds the graph, comments and gates; .cad-work/<unit>/status.json the lease."""

    def __init__(
        self, project: Path, clock: Callable[[], datetime], make_backoff: Callable[[], Backoff] = Backoff
    ) -> None:
        self.project, self.clock, self.make_backoff = project, clock, make_backoff
        self.ledger_path = project / "hw" / ".cad" / "work" / "ledger.json"
        self.lock_path = self.ledger_path.with_name(".lock")

    def _status_path(self, unit: str) -> Path:
        return self.project / ".cad-work" / _check_name("unit", unit) / "status.json"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "units": {}, "gates": {}}
        except (OSError, ValueError) as error:
            raise WorkError(f"cannot read {self.ledger_path}: {error}") from error
        if data.get("schema_version") != 1:
            raise WorkError(f"{self.ledger_path} has an unknown schema_version")
        return data  # type: ignore[no-any-return]

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[dict[str, Any]]:
        with store_lock(self.lock_path, self.make_backoff):
            data = self._load()
            yield data
            try:
                _atomic_write(self.ledger_path, data)
            except OSError as error:
                raise WorkError(f"cannot write {self.ledger_path}: {error}") from error

    def _lease(self, unit: str) -> dict[str, Any]:
        try:
            return json.loads(self._status_path(unit).read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise WorkError(f"cannot read {self._status_path(unit)}: {error}") from error

    def _set_lease(self, unit: str, lease: dict[str, Any]) -> None:
        _atomic_write(self._status_path(unit), {"unit": unit, **lease})

    @staticmethod
    def _open_gates(data: dict[str, Any], unit: str) -> tuple[str, ...]:
        return tuple(
            name
            for name, gate in sorted(data["gates"].items())
            if unit in gate["blocks"] and gate["status"] == "open" and not gate["void"]
        )

    def _units(self, data: dict[str, Any]) -> list[Unit]:
        out = []
        for name, record in data["units"].items():
            lease = self._lease(name)
            status = "closed" if record.get("closed") else "in_progress" if lease.get("assignee") else "open"
            out.append(
                Unit(
                    name,
                    record["stage"],
                    name,
                    status,
                    lease.get("assignee"),
                    lease.get("heartbeat_at"),
                    lease.get("lease_expires_at"),
                    tuple(record["deps"]),
                    self._open_gates(data, name),
                )
            )
        return out

    def units(self) -> list[Unit]:
        return self._units(self._load())

    def unit(self, unit: str) -> Unit:
        return self._unit(self._load(), unit)

    def gate(self, name: str) -> Gate | None:
        return next((gate for gate in self.gates() if gate.name == name), None)

    def _unit(self, data: dict[str, Any], unit: str) -> Unit:
        found = next((item for item in self._units(data) if item.unit == unit), None)
        if found is None:
            raise WorkError(f"no unit {unit!r} in the ledger")
        return found

    def add_units(self, specs: Sequence[UnitSpec]) -> list[str]:
        created = []
        with self._transaction() as data:
            for spec in specs:
                if spec.unit not in data["units"]:
                    data["units"][_check_name("unit", spec.unit)] = {
                        "stage": spec.stage,
                        "title": spec.title or spec.unit,
                        "deps": [],
                        "comments": [],
                    }
                    created.append(spec.unit)
            for spec in specs:
                for dep in spec.deps:
                    if dep not in data["units"]:
                        raise WorkError(f"unit {spec.unit!r} depends on unknown unit {dep!r}")
                    if dep not in data["units"][spec.unit]["deps"]:
                        data["units"][spec.unit]["deps"].append(dep)
        return created

    def _blockers(self, data: dict[str, Any], item: Unit) -> tuple[str, ...]:
        closed = {other.unit for other in self._units(data) if other.status == "closed"}
        return (*(dep for dep in item.deps if dep not in closed), *(f"gate:{gate}" for gate in item.gates))

    def ready(self) -> list[Unit]:
        data = self._load()
        return [item for item in self._units(data) if item.status == "open" and not self._blockers(data, item)]

    def blocked(self) -> list[Unit]:
        data = self._load()
        return [
            Unit(**{**asdict(item), "blocked_by": self._blockers(data, item)})
            for item in self._units(data)
            if item.status == "open" and self._blockers(data, item)
        ]

    def _hold(self, unit: str, actor: str, renew_only: bool) -> Unit:
        with self._transaction() as data:
            item = self._unit(data, unit)
            if item.status == "closed":
                raise ClaimError(f"{unit} is closed")
            if item.assignee not in (None, actor):
                raise ClaimError(f"{unit} is already claimed by {item.assignee}")
            if renew_only and item.assignee != actor:
                raise ClaimError(f"{unit} is not claimed by {actor}")
            now = self.clock()
            lease = self._lease(unit)
            self._set_lease(
                unit,
                {
                    "assignee": actor,
                    "started_at": lease.get("started_at") or _stamp(now),
                    "heartbeat_at": _stamp(now),
                    "lease_expires_at": _stamp(now + LEASE_TTL),
                },
            )
            return self._unit(data, unit)

    def claim(self, unit: str, actor: str) -> Unit:
        return self._hold(unit, actor, renew_only=False)

    def heartbeat(self, unit: str, actor: str) -> Unit:
        return self._hold(unit, actor, renew_only=True)

    def unclaim(self, unit: str, holder: str, reason: str) -> None:
        with self._transaction() as data:
            item = self._unit(data, unit)
            if item.assignee != holder:
                raise ClaimError(f"assignee mismatch: {unit} is held by {item.assignee!r}, expected {holder!r}")
            self._set_lease(unit, {})

    def _record(self, data: dict[str, Any], unit: str) -> dict[str, Any]:
        if unit not in data["units"]:
            raise WorkError(f"no unit {unit!r} in the ledger")
        return data["units"][unit]  # type: ignore[no-any-return]

    def comment(self, unit: str, text: str, author: str) -> None:
        with self._transaction() as data:
            self._record(data, unit)["comments"].append(
                {"author": author, "text": text, "created_at": _stamp(self.clock())}
            )

    def comments(self, unit: str) -> list[str]:
        return [item["text"] for item in self._record(self._load(), unit)["comments"]]

    def close(self, unit: str, reason: str, actor: str) -> None:
        with self._transaction() as data:
            item = self._unit(data, unit)
            if item.assignee not in (None, actor):
                raise ClaimError(f"cannot close {unit}: assignee is {item.assignee!r}, actor is {actor!r}")
            self._record(data, unit)["closed"] = {"reason": reason, "at": _stamp(self.clock())}
            self._set_lease(unit, {})

    def reopen(self, unit: str, reason: str) -> None:
        with self._transaction() as data:
            if self._record(data, unit).pop("closed", None) is not None:
                self._set_lease(unit, {})  # a unit that was not closed keeps its claim, live or stale

    def gates(self) -> list[Gate]:
        return [
            Gate(name, f"gate:{name}", gate["status"], gate["void"], tuple(gate["blocks"]), tuple(gate["waiters"]))
            for name, gate in sorted(self._load()["gates"].items())
        ]

    def _gate_record(self, data: dict[str, Any], name: str) -> dict[str, Any]:
        if name not in data["gates"]:
            raise WorkError(f"no gate {name!r} in the ledger")
        return data["gates"][name]  # type: ignore[no-any-return]

    def gate_create(self, name: str, blocks: Sequence[str], reason: str) -> bool:
        with self._transaction() as data:
            if name in data["gates"]:
                return False
            for unit in blocks:
                self._record(data, unit)
            data["gates"][_check_name("gate", name)] = {
                "status": "open",
                "void": False,
                "reason": reason,
                "blocks": list(blocks),
                "waiters": [],
                "comments": [],
            }
        return True

    def gate_block(self, name: str, unit: str) -> None:
        with self._transaction() as data:
            self._record(data, unit)
            blocks = self._gate_record(data, name)["blocks"]
            if unit not in blocks:
                blocks.append(unit)

    def gate_add_waiter(self, name: str, waiter: str) -> None:
        with self._transaction() as data:
            waiters = self._gate_record(data, name)["waiters"]
            if waiter not in waiters:
                waiters.append(waiter)

    def gate_comment(self, name: str, text: str) -> None:
        with self._transaction() as data:
            self._gate_record(data, name)["comments"].append(
                {"author": FLOW_ACTOR, "text": text, "created_at": _stamp(self.clock())}
            )

    def gate_comments(self, name: str) -> list[str]:
        return [item["text"] for item in self._gate_record(self._load(), name)["comments"]]

    def gate_void(self, old: str, new: str, reason: str) -> None:
        with self._transaction() as data:  # one write: a refusal or a failure leaves the ledger as it was
            previous = self._gate_record(data, old)
            existing = data["gates"].get(new)
            stage_of = {name: record["stage"] for name, record in data["units"].items()}
            check_successor(
                self._gate_of(old, previous), new, self._gate_of(new, existing) if existing else None, stage_of
            )
            if not previous["blocks"]:
                raise WorkError(f"gate {old} blocks no unit, so there is nothing to move to a successor")
            for unit in previous["blocks"]:
                self._record(data, unit)
            note = f"supersedes {old}: {reason}"
            successor = data["gates"].setdefault(
                _check_name("gate", new),
                {"status": "open", "void": False, "reason": note, "blocks": [], "waiters": [], "comments": []},
            )
            successor["blocks"] += [unit for unit in previous["blocks"] if unit not in successor["blocks"]]
            successor["waiters"] += [w for w in previous["waiters"] if w not in successor["waiters"]]
            if note not in [comment["text"] for comment in successor["comments"]]:
                successor["comments"].append({"author": FLOW_ACTOR, "text": note, "created_at": _stamp(self.clock())})
            previous["void"] = True
            if previous["status"] == "open":
                previous.update(status="closed", close_reason=f"void: superseded by {new}")

    @staticmethod
    def _gate_of(name: str, record: dict[str, Any]) -> Gate:
        return Gate(
            name, f"gate:{name}", record["status"], record["void"], tuple(record["blocks"]), tuple(record["waiters"])
        )

    def _gate_close(self, name: str, reason: str) -> None:
        with self._transaction() as data:
            self._gate_record(data, name).update(status="closed", close_reason=reason)


# --- the ledger ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    code: str
    gate: str
    message: str


def use_bd(project: Path) -> bool:
    return (project / ".beads").is_dir()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Ledger:
    """The one interface to the work store; `backend` is bd when the project has .beads, else the file ledger."""

    backend: Backend
    clock: Callable[[], datetime] = field(default=utc_now)

    @classmethod
    def open(
        cls,
        project: Path,
        clock: Callable[[], datetime] = utc_now,
        bd: str = "bd",
        make_backoff: Callable[[], Backoff] = Backoff,
        bd_timeout: float = BD_TIMEOUT_S,
    ) -> Ledger:
        if use_bd(project):
            version = bd_version(bd, bd_timeout)
            if version is None or version < MIN_BD:
                raise WorkError("the project has .beads but bd >= " + ".".join(map(str, MIN_BD)) + " is not available")
            return cls(BdBackend(project, bd, make_backoff, bd_timeout), clock)
        return cls(FileBackend(project, clock, make_backoff), clock)

    def create_graph(self, specs: Sequence[UnitSpec]) -> list[str]:
        for spec in specs:
            _check_name("unit", spec.unit)
        return self.backend.add_units(specs)

    def units(self) -> list[Unit]:
        return self.backend.units()

    def ready(self, flow_gates: Collection[str]) -> list[Unit]:
        """Units to dispatch, given the gates flow.py accepted (an empty set: nothing is approved yet).

        A unit behind a gate that is closed here without an entry in `flow_gates` stays blocked; `findings` reports
        the gate. `claim` uses this same guard.
        """
        held = {
            unit
            for gate in self.backend.gates()
            if gate.status == "closed" and not gate.void and gate.name not in flow_gates
            for unit in gate.blocks
        }
        return [item for item in self.backend.ready() if item.unit not in held]

    def blocked(self) -> list[Unit]:
        return self.backend.blocked()

    def stale(self) -> list[Unit]:
        """Claimed units whose lease has expired: their worker stopped beating."""
        now = self.clock()
        return [
            item
            for item in self.backend.units()
            if item.status == "in_progress" and item.lease_expires_at and _parse(item.lease_expires_at) < now
        ]

    def claim(self, unit: str, flow_gates: Collection[str], actor: str | None = None) -> Unit:
        """Claim a ready unit as `actor` (default cad/<unit>). bd also claims a blocked unit, so check here."""
        mine = self.backend.unit(unit)
        if mine.status == "open" and unit not in {item.unit for item in self.ready(flow_gates)}:
            raise ClaimError(f"{unit} is not ready")
        return self.backend.claim(unit, actor or actor_of(unit))

    def heartbeat(self, unit: str, actor: str | None = None) -> Unit:
        return self.backend.heartbeat(unit, actor or actor_of(unit))

    def unclaim(self, unit: str, holder: str, reason: str = "released") -> None:
        self.backend.unclaim(unit, holder, reason)

    def comment(self, unit: str, text: str, actor: str | None = None) -> None:
        self.backend.comment(unit, text, actor or actor_of(unit))

    def evidence(self, unit: str, path: Path, actor: str | None = None) -> str:
        """Comment `<abs path> sha256:<digest>` for a file; return the comment."""
        import hashlib

        absolute = path.resolve()
        text = f"{absolute} sha256:{hashlib.sha256(absolute.read_bytes()).hexdigest()}"
        self.comment(unit, text, actor)
        return text

    def comments(self, unit: str) -> list[str]:
        return self.backend.comments(unit)

    def close(self, unit: str, reason: str = "done", actor: str | None = None) -> None:
        self.backend.close(unit, reason, actor or actor_of(unit))

    def reopen(self, unit: str, reason: str = "reopened") -> None:
        self.backend.reopen(unit, reason)

    def gates(self) -> list[Gate]:
        return self.backend.gates()

    def create_gate(self, name: str, blocks: Sequence[str], reason: str = "") -> bool:
        if not blocks:
            raise WorkError("a gate must block at least one unit")
        return self.backend.gate_create(_check_name("gate", name), blocks, reason or name)

    def add_gate_waiter(self, name: str, waiter: str) -> None:
        self.backend.gate_add_waiter(name, waiter)

    def void_gate(self, old: str, new: str, reopen: Sequence[str] = (), reason: str = "voided") -> None:
        """Void gate `old`: open successor `new` on the units it blocked, retire `old`, reopen `reopen`.

        The backend validates the successor and creates it first, and retires the old gate only then, so a refusal
        leaves the ledger as it was. The steps are separate writes on bd, and the reopens follow the gate write on
        both backends. A crash between them leaves a ledger that is safe (units stay blocked behind the successor)
        and incomplete; the same call again, with the same arguments, repeats nothing and finishes the rest.
        This resolves nothing: a void gate is never reported as a mismatch.
        """
        _check_name("gate", new)
        missing = sorted(set(reopen) - {item.unit for item in self.backend.units()})
        if missing:
            raise WorkError(f"cannot reopen unknown units: {', '.join(missing)}")
        self.backend.gate_void(old, new, reason)
        for unit in reopen:
            self.backend.reopen(unit, f"gate {old} voided")

    def findings(self, flow_gates: Collection[str]) -> list[Finding]:
        """`ledger.gate_unbound` for a gate closed here that flow.gates lacks, or recorded there but not closed here."""
        ledger = {gate.name: gate for gate in self.backend.gates() if not gate.void}
        out = [
            Finding(GATE_UNBOUND, name, f"gate {name} is closed in the ledger but flow.gates has no entry for it")
            for name, gate in sorted(ledger.items())
            if gate.status == "closed" and name not in flow_gates
        ]
        for name in sorted(flow_gates):
            gate = ledger.get(name)
            if gate is None or gate.status != "closed":
                state = "has no ledger gate" if gate is None else "is still open in the ledger"
                out.append(Finding(GATE_UNBOUND, name, f"flow.gates records gate {name} but it {state}"))
        return out


# TODO(flow.py, plan item 4: `flow.py advance`): call resolve_gate right after advance accepts the decision
# record, and pass the gate names of flow.gates to Ledger.findings from `flow.py status`. Nothing else may close a gate.
def resolve_gate(ledger: Ledger, name: str, decision: AcceptedDecision) -> None:
    """Close gate `name` for an accepted decision and comment the decision id, version and evidence digest."""
    if not isinstance(decision, AcceptedDecision):
        raise TypeError("resolve_gate needs an AcceptedDecision")
    gate = ledger.backend.gate(name)
    if gate is None or gate.void:
        raise WorkError(f"no live gate {name!r} to resolve")
    if gate.status == "closed":
        raise WorkError(f"gate {name} is already resolved")
    ledger.backend._gate_close(name, decision.note)
    ledger.backend.gate_comment(name, decision.note)


# --- the accepted-gate set ----------------------------------------------------------------------------------------


GATE_STATES = ("provisional", "approved")  # the recorded states of the 01 flow schema; no entry is `unrecorded`
# The gated stages and their ledger gates, from 02-process-design, "Beads work tracker": "Single gates: intent,
# variant, freeze, release" are the gates of the stages intent, concept, design_freeze and release; every other
# gated stage is two-step, with the gates "<stage>.provisional" and "<stage>.approval".
SINGLE_GATES = {"intent": "intent", "concept": "variant", "design_freeze": "freeze", "release": "release"}
TWO_STEP_STAGES = ("layout", "parts", "detail", "analysis")


def _yaml_loader() -> Any:
    """cad-record's shared YAML reader, loaded by path once per process (as harness/generate.py does)."""
    name = "cad_record_yaml_loader"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "cad-record" / "scripts" / "yaml_loader.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise WorkError(f"the YAML loader {path} was not found")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError as error:
            raise WorkError("reading flow.gates needs PyYAML; run work.py with `uv run --script`") from error
        sys.modules[name] = module
    return sys.modules[name]


def _bound_gates(stage: str, entry: dict[str, Any]) -> set[str]:
    """The ledger gates a recorded stage entry binds.

    `provisional` binds the provisional gate of a two-step stage. `approved` binds its provisional and approval gates,
    since 01 has a two-step approval cite the provisional decision, or the single gate of a single-gate stage.
    """
    if stage in SINGLE_GATES:
        return {SINGLE_GATES[stage]}
    if entry["state"] == "provisional":
        return {f"{stage}.provisional"}
    return {f"{stage}.provisional", f"{stage}.approval"}


def flow_gates_from_product(project: Path) -> frozenset[str]:
    """The ledger gates bound by flow.gates in <project>/hw/product.yaml, read only.

    No file, no `flow`, or no `gates` means nothing is approved yet. Otherwise `gates` must be the 01 schema's mapping
    from a gated stage to {state: provisional|approved, version, provisional, approved}; any other shape, or a key
    that is not a gated stage, is a WorkError.
    """
    path = project / "hw" / "product.yaml"
    if not path.is_file():
        return frozenset()
    loader = _yaml_loader()
    try:
        product = loader.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, loader.yaml.YAMLError) as error:
        raise WorkError(f"cannot read {path}: {error}") from error
    flow = product.get("flow") if isinstance(product, dict) else None
    gates = flow.get("gates") if isinstance(flow, dict) else None
    if gates is None:
        return frozenset()
    if not isinstance(gates, dict):
        raise WorkError(f"{path}: flow.gates must be a mapping from stage to entry, not a {type(gates).__name__}")
    bound: set[str] = set()
    for stage, entry in gates.items():
        if not isinstance(stage, str):
            raise WorkError(f"{path}: flow.gates has a stage key that is not a name: {stage!r}")
        if stage not in SINGLE_GATES and stage not in TWO_STEP_STAGES:
            gated = ", ".join((*SINGLE_GATES, *TWO_STEP_STAGES))
            raise WorkError(f"{path}: flow.gates.{stage} is not a gated stage (the gated stages are {gated})")
        if not isinstance(entry, dict):
            raise WorkError(f"{path}: flow.gates.{stage} must be a mapping, not a {type(entry).__name__}")
        if "state" not in entry:
            raise WorkError(f"{path}: flow.gates.{stage} has no state")
        if entry["state"] not in GATE_STATES:
            raise WorkError(f"{path}: flow.gates.{stage}.state must be provisional or approved, not {entry['state']!r}")
        if stage in SINGLE_GATES and entry["state"] != "approved":
            raise WorkError(f"{path}: flow.gates.{stage} is a single-gate stage; its state can only be approved")
        bound |= _bound_gates(stage, entry)
    return frozenset(bound)


# --- command line -------------------------------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return asdict(value) if hasattr(value, "__dataclass_fields__") else value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--project", type=Path, default=Path.cwd(), help="project root (default: the current directory)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the bd store (without git hooks or agent files)")
    sub.add_parser("graph", help="create the units and dependencies of a graph file").add_argument(
        "--file", type=Path, required=True
    )
    for name in ("units", "blocked", "stale", "gates"):
        sub.add_parser(name)
    sub.add_parser("ready", help="units to dispatch, behind the accepted gates of hw/product.yaml flow.gates")
    for name in ("claim", "heartbeat"):
        command = sub.add_parser(name)
        command.add_argument("unit")
        command.add_argument("--actor")
    command = sub.add_parser("unclaim")
    command.add_argument("unit")
    command.add_argument("--holder", required=True)
    command.add_argument("--reason", default="released")
    command = sub.add_parser("comment")
    command.add_argument("unit")
    command.add_argument("text")
    command.add_argument("--actor")
    command = sub.add_parser("evidence", help="comment `<abs path> sha256:<digest>` for a file")
    command.add_argument("unit")
    command.add_argument("path", type=Path)
    command.add_argument("--actor")
    sub.add_parser("comments").add_argument("unit")
    command = sub.add_parser("close")
    command.add_argument("unit")
    command.add_argument("--reason", default="done")
    command.add_argument("--actor")
    command = sub.add_parser("reopen")
    command.add_argument("unit")
    command.add_argument("--reason", default="reopened")
    command = sub.add_parser("gate-create")
    command.add_argument("name")
    command.add_argument("--blocks", nargs="+", required=True)
    command.add_argument("--reason", default="")
    command = sub.add_parser("gate-waiter")
    command.add_argument("name")
    command.add_argument("waiter")
    command = sub.add_parser("gate-void", help="void a gate and open its successor; resolves nothing")
    command.add_argument("old")
    command.add_argument("new")
    command.add_argument("--reopen", nargs="*", default=[])
    command.add_argument("--reason", default="voided")
    sub.add_parser("findings")
    return parser


def _graph(path: Path) -> list[UnitSpec]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            UnitSpec(item["unit"], item["stage"], item.get("title", ""), tuple(item.get("deps", ()))) for item in raw
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkError(f"cannot read graph {path}: {error}") from error


def run(args: argparse.Namespace) -> Any:
    project = args.project.resolve()
    if args.command == "init":
        bd_init(project)
        return {"backend": "bd"}
    ledger = Ledger.open(project)
    command = args.command
    if command == "graph":
        return {"created": ledger.create_graph(_graph(args.file))}
    if command == "ready":
        return _plain(ledger.ready(flow_gates_from_product(project)))
    if command in ("units", "blocked", "stale", "gates"):
        return _plain(getattr(ledger, command)())
    if command == "claim":
        return _plain(ledger.claim(args.unit, flow_gates_from_product(project), args.actor))
    if command == "heartbeat":
        return _plain(ledger.heartbeat(args.unit, args.actor))
    if command == "unclaim":
        ledger.unclaim(args.unit, args.holder, args.reason)
    elif command == "comment":
        ledger.comment(args.unit, args.text, args.actor)
    elif command == "evidence":
        return {"comment": ledger.evidence(args.unit, args.path, args.actor)}
    elif command == "comments":
        return ledger.comments(args.unit)
    elif command == "close":
        ledger.close(args.unit, args.reason, args.actor)
    elif command == "reopen":
        ledger.reopen(args.unit, args.reason)
    elif command == "gate-create":
        return {"created": ledger.create_gate(args.name, args.blocks, args.reason)}
    elif command == "gate-waiter":
        ledger.add_gate_waiter(args.name, args.waiter)
    elif command == "gate-void":
        ledger.void_gate(args.old, args.new, args.reopen, args.reason)
    elif command == "findings":
        return _plain(ledger.findings(flow_gates_from_product(project)))
    return {"ok": True}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(run(args), indent=2))
    except WorkError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
