#!/usr/bin/env python3
"""cad-flow work ledger tests: units, claims, leases and gates behave the same on bd and on the file ledger.

Run: python3 -m unittest discover -s skills/cad-flow/tests -p 'test_work.py' -v
Stdlib only. Three layers:
- the scenarios run on the file backend and, when bd >= 1.3.1 is on PATH, on a real bd store;
- a fake `bd` on PATH replays output recorded from bd 1.3.1 (tests/fixtures/bd-1.3.1) to pin the argv
  work.py sends and the JSON it parses, without bd installed;
- busy-store retries, the lock, and the CLI surface.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "bd-1.3.1"
SCRIPT = HERE.parent / "scripts" / "work.py"
SCRATCH = HERE.parent / "scripts" / "scratch.py"
TEMPLATE = HERE.parents[1] / "cad-record" / "assets" / "project-template" / "hw"
spec = importlib.util.spec_from_file_location("cad_work", SCRIPT)
assert spec is not None and spec.loader is not None
work = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = work
spec.loader.exec_module(work)

REAL_BD = work.bd_version()
HAVE_BD = REAL_BD is not None and REAL_BD >= work.MIN_BD
DECISION = work.AcceptedDecision("DR-001", 1, "ab" * 32)
NO_GATES: frozenset[str] = frozenset()
GRAPH = [
    work.UnitSpec("a", "layout"),
    work.UnitSpec("b", "layout", deps=("a",)),
    work.UnitSpec("c", "layout", deps=("a", "b")),
]


def names(units) -> list[str]:
    return sorted(item.unit for item in units)


def hooks_path(project: Path) -> str:
    done = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=project, capture_output=True, text=True)
    return done.stdout.strip()


def make_project(root: Path) -> Path:
    """A git project whose core.hooksPath is a sentinel that work.py must leave alone."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "config", "core.hooksPath", ".sentinel-hooks"], cwd=root, check=True)
    return root


class Scenarios:
    """Behaviour every backend owes; subclasses set `self.project`, `self.ledger`, `hand_close`."""

    project: Path
    ledger: "work.Ledger"
    offset: timedelta

    def hand_close(self, gate: str) -> None:
        raise NotImplementedError

    def advance(self, **delta: float) -> None:
        self.offset += timedelta(**delta)

    def tearDown(self) -> None:  # noqa: N802
        self.assertEqual(hooks_path(self.project), ".sentinel-hooks")

    def test_graph_creation_is_idempotent(self) -> None:
        self.assertEqual(self.ledger.create_graph(GRAPH), ["a", "b", "c"])
        self.assertEqual(self.ledger.create_graph(GRAPH), [])
        units = {item.unit: item for item in self.ledger.units()}
        self.assertEqual(sorted(units), ["a", "b", "c"])
        self.assertEqual(
            {name: tuple(sorted(item.deps)) for name, item in units.items()}, {"a": (), "b": ("a",), "c": ("a", "b")}
        )
        self.assertEqual({item.stage for item in units.values()}, {"layout"})

    def test_ready_and_blocked_follow_dependencies(self) -> None:
        self.ledger.create_graph(GRAPH)
        self.assertEqual(names(self.ledger.ready(NO_GATES)), ["a"])
        blocked = {item.unit: item for item in self.ledger.blocked()}
        self.assertEqual(sorted(blocked), ["b", "c"])
        self.assertEqual(set(blocked["b"].blocked_by), {"a"})
        self.assertEqual(set(blocked["c"].blocked_by), {"a", "b"})
        self.ledger.claim("a", NO_GATES)
        self.ledger.close("a")
        self.assertEqual(names(self.ledger.ready(NO_GATES)), ["b"])

    def test_claim_and_release_check_the_assignee(self) -> None:
        self.ledger.create_graph(GRAPH)
        claimed = self.ledger.claim("a", NO_GATES)
        self.assertEqual((claimed.status, claimed.assignee), ("in_progress", "cad/a"))
        self.assertEqual(names(self.ledger.ready(NO_GATES)), [])
        with self.assertRaises(work.ClaimError):
            self.ledger.claim("a", NO_GATES, "cad/x")
        with self.assertRaises(work.ClaimError):
            self.ledger.heartbeat("a", "cad/x")
        with self.assertRaises(work.ClaimError):
            self.ledger.unclaim("a", "cad/x")
        self.assertEqual(next(i for i in self.ledger.units() if i.unit == "a").assignee, "cad/a")
        self.ledger.unclaim("a", "cad/a")
        released = next(i for i in self.ledger.units() if i.unit == "a")
        self.assertEqual((released.status, released.assignee), ("open", None))
        self.assertEqual(names(self.ledger.ready(NO_GATES)), ["a"])

    def test_a_blocked_unit_cannot_be_claimed(self) -> None:
        self.ledger.create_graph(GRAPH)
        with self.assertRaises(work.ClaimError):
            self.ledger.claim("b", NO_GATES)

    def test_lease_lasts_five_minutes_then_the_unit_is_stale(self) -> None:
        self.ledger.create_graph(GRAPH)
        claimed = self.ledger.claim("a", NO_GATES)
        assert claimed.lease_expires_at and claimed.heartbeat_at
        self.assertEqual(
            work._parse(claimed.lease_expires_at) - work._parse(claimed.heartbeat_at), timedelta(minutes=5)
        )
        self.assertEqual(self.ledger.stale(), [])
        self.advance(minutes=4)
        self.assertEqual(self.ledger.stale(), [])
        self.advance(minutes=2)
        self.assertEqual(names(self.ledger.stale()), ["a"])
        self.ledger.unclaim("a", "cad/a", "stale")
        self.assertEqual(self.ledger.stale(), [])

    def test_heartbeat_keeps_the_claim_and_never_moves_the_lease_back(self) -> None:
        self.ledger.create_graph(GRAPH)
        before = self.ledger.claim("a", NO_GATES)
        after = self.ledger.heartbeat("a")
        assert before.lease_expires_at and after.lease_expires_at
        self.assertEqual((after.status, after.assignee), ("in_progress", "cad/a"))
        self.assertGreaterEqual(after.lease_expires_at, before.lease_expires_at)

    def test_evidence_comments_are_kept_in_order(self) -> None:
        self.ledger.create_graph(GRAPH)
        evidence = self.project / "evidence.json"
        evidence.write_text("{}\n", encoding="utf-8")
        text = self.ledger.evidence("a", evidence)
        digest = hashlib.sha256(b"{}\n").hexdigest()
        self.assertEqual(text, f"{evidence.resolve()} sha256:{digest}")
        self.ledger.comment("a", "second note")
        self.assertEqual(self.ledger.comments("a"), [text, "second note"])

    def test_close_checks_the_assignee_and_reopen_releases_the_unit(self) -> None:
        self.ledger.create_graph(GRAPH)
        self.ledger.claim("a", NO_GATES)
        with self.assertRaises(work.ClaimError):
            self.ledger.close("a", actor="cad/x")
        self.ledger.close("a")
        self.assertEqual(next(i for i in self.ledger.units() if i.unit == "a").status, "closed")
        self.ledger.reopen("a", "void")
        reopened = next(i for i in self.ledger.units() if i.unit == "a")
        self.assertEqual((reopened.status, reopened.assignee), ("open", None))
        self.assertEqual(self.ledger.claim("a", NO_GATES, "cad/y").assignee, "cad/y")

    def test_a_gate_blocks_until_resolve_gate_closes_it(self) -> None:
        self.ledger.create_graph([work.UnitSpec("a", "layout"), work.UnitSpec("b", "layout")])
        self.assertTrue(self.ledger.create_gate("g1", ["b"], "layout provisional"))
        self.assertFalse(self.ledger.create_gate("g1", ["b"], "layout provisional"))
        self.ledger.add_gate_waiter("g1", "cad/b")
        gate = next(g for g in self.ledger.gates() if g.name == "g1")
        self.assertEqual((gate.status, gate.blocks, gate.waiters), ("open", ("b",), ("cad/b",)))
        self.assertEqual(names(self.ledger.ready(NO_GATES)), ["a"])
        self.assertIn("gate:g1", next(i for i in self.ledger.blocked() if i.unit == "b").blocked_by)
        work.resolve_gate(self.ledger, "g1", DECISION)
        self.assertEqual(names(self.ledger.ready(frozenset({"g1"}))), ["a", "b"])
        self.assertEqual(self.ledger.backend.gate_comments("g1"), [DECISION.note])
        with self.assertRaises(work.WorkError):
            work.resolve_gate(self.ledger, "g1", DECISION)

    def test_a_hand_closed_gate_is_reported_and_blocks_ready_and_claim(self) -> None:
        self.ledger.create_graph([work.UnitSpec("b", "layout")])
        self.ledger.create_gate("g1", ["b"])
        self.assertEqual(self.ledger.findings(NO_GATES), [])
        self.assertEqual([f.gate for f in self.ledger.findings(frozenset({"g1"}))], ["g1"])  # flow has it, ledger open
        self.hand_close("g1")
        found = self.ledger.findings(NO_GATES)
        self.assertEqual([(f.code, f.gate) for f in found], [(work.GATE_UNBOUND, "g1")])
        self.assertEqual(names(self.ledger.backend.ready()), ["b"])  # the store alone would dispatch it
        self.assertEqual(names(self.ledger.ready(NO_GATES)), [])
        with self.assertRaises(work.ClaimError):
            self.ledger.claim("b", NO_GATES)
        self.assertEqual(self.ledger.findings(frozenset({"g1"})), [])
        self.assertEqual(names(self.ledger.ready(frozenset({"g1"}))), ["b"])
        self.assertEqual(self.ledger.claim("b", frozenset({"g1"})).assignee, "cad/b")

    def void_setup(self) -> None:
        """Units i (closed) and n; gate g1 blocks n and is resolved, with a waiter registered."""
        self.ledger.create_graph([work.UnitSpec("i", "layout"), work.UnitSpec("n", "next")])
        self.ledger.claim("i", NO_GATES)
        self.ledger.close("i")
        self.ledger.create_gate("g1", ["n"])
        self.ledger.add_gate_waiter("g1", "cad/n")
        work.resolve_gate(self.ledger, "g1", DECISION)

    def assert_nothing_retired(self) -> None:
        gates = {g.name: g for g in self.ledger.gates()}
        self.assertEqual((gates["g1"].status, gates["g1"].void), ("closed", False))
        self.assertEqual(next(i for i in self.ledger.units() if i.unit == "i").status, "closed")
        self.assertEqual(names(self.ledger.ready(frozenset({"g1"}))), ["n"])

    def test_a_void_gate_gets_a_successor_with_the_waiters_and_the_units_reopen(self) -> None:
        self.void_setup()
        self.assertEqual(names(self.ledger.ready(frozenset({"g1"}))), ["n"])
        self.ledger.void_gate("g1", "g1.v2", reopen=["i"], reason="geometry changed")
        gates = {g.name: g for g in self.ledger.gates()}
        self.assertTrue(gates["g1"].void)
        self.assertEqual((gates["g1.v2"].status, gates["g1.v2"].blocks), ("open", ("n",)))
        self.assertIn("cad/n", gates["g1.v2"].waiters)
        self.assertTrue(any("supersedes g1" in c for c in self.ledger.backend.gate_comments("g1.v2")))
        self.assertEqual(names(self.ledger.ready(frozenset({"g1"}))), ["i"])
        self.assertEqual(next(i for i in self.ledger.units() if i.unit == "i").status, "open")
        self.assertEqual(self.ledger.findings(NO_GATES), [])  # a void gate is not a mismatch
        self.assertEqual([f.gate for f in self.ledger.findings(frozenset({"g1.v2"}))], ["g1.v2"])

    def test_a_void_with_an_invalid_successor_name_retires_nothing(self) -> None:
        self.void_setup()
        for name in ("bad name", "../escape", "", "-lead", "g1"):
            with self.subTest(name), self.assertRaises(work.WorkError):
                self.ledger.void_gate("g1", name, reopen=["i"])
            self.assert_nothing_retired()
            self.assertEqual({g.name for g in self.ledger.gates()}, {"g1"})

    def test_a_void_onto_an_existing_closed_or_void_successor_retires_nothing(self) -> None:
        self.void_setup()
        self.ledger.create_graph([work.UnitSpec("m", "next")])
        self.ledger.create_gate("closed-one", ["m"])
        work.resolve_gate(self.ledger, "closed-one", DECISION)
        self.ledger.create_gate("void-one", ["m"])
        self.ledger.void_gate("void-one", "void-one.v2")
        for successor in ("closed-one", "void-one"):
            with self.subTest(successor), self.assertRaisesRegex(work.WorkError, "already exists and is"):
                self.ledger.void_gate("g1", successor, reopen=["i"])
            self.assert_nothing_retired()

    def test_a_void_that_would_reopen_an_unknown_unit_retires_nothing(self) -> None:
        self.void_setup()
        with self.assertRaisesRegex(work.WorkError, "unknown units: ghost"):
            self.ledger.void_gate("g1", "g1.v2", reopen=["i", "ghost"])
        self.assert_nothing_retired()
        self.assertEqual({g.name for g in self.ledger.gates()}, {"g1"})

    def test_voiding_an_open_gate_does_not_unblock_its_units(self) -> None:
        self.ledger.create_graph([work.UnitSpec("n", "next")])
        self.ledger.create_gate("g1", ["n"])
        self.ledger.void_gate("g1", "g1.v2")
        self.assertEqual(names(self.ledger.ready(NO_GATES)), [])
        self.assertEqual([g.name for g in self.ledger.gates() if g.status == "open"], ["g1.v2"])

    def test_a_void_onto_an_open_successor_moves_the_blocked_units_so_nothing_becomes_ready(self) -> None:
        self.ledger.create_graph([work.UnitSpec("n1", "next"), work.UnitSpec("n2", "next")])
        self.ledger.create_gate("g1", ["n1", "n2"])
        self.ledger.add_gate_waiter("g1", "cad/n2")
        self.ledger.create_gate("g2", ["n1"])  # an open successor that so far blocks one of the two units
        self.ledger.void_gate("g1", "g2")
        gates = {g.name: g for g in self.ledger.gates()}
        self.assertEqual((gates["g1"].void, gates["g1"].status), (True, "closed"))
        self.assertEqual((set(gates["g2"].blocks), gates["g2"].status), ({"n1", "n2"}, "open"))
        self.assertIn("cad/n2", gates["g2"].waiters)
        self.assertEqual(names(self.ledger.ready(NO_GATES)), [])
        with self.assertRaises(work.ClaimError):
            self.ledger.claim("n2", NO_GATES)

    def test_a_void_onto_a_successor_that_blocks_another_stage_is_refused_and_retires_nothing(self) -> None:
        self.ledger.create_graph([work.UnitSpec("n1", "next"), work.UnitSpec("m", "other")])
        self.ledger.create_gate("g1", ["n1"])
        self.ledger.create_gate("g3", ["m"])
        with self.assertRaisesRegex(work.WorkError, "already blocks"):
            self.ledger.void_gate("g1", "g3")
        gates = {g.name: g for g in self.ledger.gates()}
        self.assertEqual((gates["g1"].void, gates["g1"].status, gates["g1"].blocks), (False, "open", ("n1",)))
        self.assertEqual((gates["g3"].status, gates["g3"].blocks), ("open", ("m",)))
        self.assertEqual(names(self.ledger.ready(NO_GATES)), [])

    VOID_FAULTS = ("gate_comment", "_gate_close", "reopen")  # backend steps a crash can interrupt

    def test_a_void_interrupted_between_steps_finishes_on_a_rerun_with_the_same_end_state(self) -> None:
        def crash(*args, **kwargs):
            raise RuntimeError("crash")

        for index, fault in enumerate(self.VOID_FAULTS):
            with self.subTest(fault):
                i, n, old, new = f"i{index}", f"n{index}", f"g{index}", f"g{index}.v2"
                self.ledger.create_graph([work.UnitSpec(i, "layout"), work.UnitSpec(n, "next")])
                self.ledger.claim(i, NO_GATES)
                self.ledger.close(i)
                self.ledger.create_gate(old, [n])
                self.ledger.add_gate_waiter(old, f"cad/{n}")
                work.resolve_gate(self.ledger, old, DECISION)
                with mock.patch.object(self.ledger.backend, fault, side_effect=crash):
                    try:
                        self.ledger.void_gate(old, new, reopen=[i], reason="geometry changed")
                    except RuntimeError:
                        pass  # the process died here; a file backend never reaches the steps it does in one write
                self.ledger.void_gate(old, new, reopen=[i], reason="geometry changed")  # the rerun
                self.ledger.void_gate(old, new, reopen=[i], reason="geometry changed")  # and a rerun of the rerun
                gates = {g.name: g for g in self.ledger.gates()}
                self.assertEqual((gates[old].void, gates[old].status), (True, "closed"))
                self.assertEqual((gates[new].void, gates[new].status), (False, "open"))
                self.assertEqual((gates[new].blocks, gates[new].waiters), ((n,), (f"cad/{n}",)))
                self.assertEqual(self.ledger.backend.gate_comments(new), ["supersedes %s: geometry changed" % old])
                self.assertEqual(self.ledger.backend.gate_comments(old), [DECISION.note])
                units = {u.unit: (u.status, u.assignee) for u in self.ledger.units()}
                self.assertEqual((units[i], units[n]), (("open", None), ("open", None)))
                self.assertNotIn(n, names(self.ledger.ready(frozenset({old}))))

    def test_reopen_keeps_a_live_claim_across_a_second_void(self) -> None:
        self.ledger.create_graph([work.UnitSpec("a", "layout")])
        self.ledger.claim("a", NO_GATES)
        self.ledger.close("a")
        self.ledger.reopen("a")  # the first VOID
        self.assertEqual(self.ledger.claim("a", NO_GATES, "cad/y").assignee, "cad/y")  # redispatched
        self.ledger.reopen("a")  # a second VOID while the worker is live
        held = next(i for i in self.ledger.units() if i.unit == "a")
        self.assertEqual((held.status, held.assignee), ("in_progress", "cad/y"))
        self.ledger.close("a", actor="cad/y")
        self.ledger.reopen("a")
        idle = next(i for i in self.ledger.units() if i.unit == "a")
        self.assertEqual((idle.status, idle.assignee), ("open", None))

    def accept(self, text: str) -> frozenset[str]:
        (self.project / "hw").mkdir(exist_ok=True)
        (self.project / "hw" / "product.yaml").write_text(text, encoding="utf-8")
        return work.flow_gates_from_product(self.project)

    def test_a_single_gate_named_differently_from_its_stage_binds_only_when_its_stage_is_approved(self) -> None:
        for stage, gate in (("concept", "variant"), ("design_freeze", "freeze")):
            with self.subTest(stage):
                unit = f"after-{gate}"
                self.ledger.create_graph([work.UnitSpec(unit, "next")])
                self.ledger.create_gate(gate, [unit])
                self.ledger.backend._gate_close(gate, "closed")
                for text in ("", "flow:\n  gates:\n    intent: {state: approved, version: v1}\n"):
                    accepted = self.accept(text)  # no entry for the stage, or an entry for another one
                    self.assertNotIn(unit, names(self.ledger.ready(accepted)))
                    with self.assertRaises(work.ClaimError):
                        self.ledger.claim(unit, accepted)
                    self.assertEqual([f.gate for f in self.ledger.findings(accepted) if f.gate == gate], [gate])
                accepted = self.accept(f"flow:\n  gates:\n    {stage}: {{state: approved, version: v1}}\n")
                self.assertEqual(accepted, frozenset({gate}))
                self.assertIn(unit, names(self.ledger.ready(accepted)))
                self.assertEqual(self.ledger.claim(unit, accepted).assignee, f"cad/{unit}")
                self.assertEqual([f for f in self.ledger.findings(accepted) if f.gate == gate], [])
                self.ledger.unclaim(unit, f"cad/{unit}")

    def test_no_operation_touches_core_hooks_path(self) -> None:
        steps = [
            lambda: self.ledger.create_graph(GRAPH),
            lambda: self.ledger.ready(NO_GATES),
            lambda: self.ledger.blocked(),
            lambda: self.ledger.claim("a", NO_GATES),
            lambda: self.ledger.heartbeat("a"),
            lambda: self.ledger.comment("a", "note"),
            lambda: self.ledger.stale(),
            lambda: self.ledger.close("a"),
            lambda: self.ledger.reopen("a"),
            lambda: self.ledger.create_gate("g1", ["c"]),
            lambda: self.ledger.add_gate_waiter("g1", "cad/c"),
            lambda: work.resolve_gate(self.ledger, "g1", DECISION),
            lambda: self.ledger.void_gate("g1", "g1.v2", reopen=["a"]),
        ]
        for step in steps:
            step()
            self.assertEqual(hooks_path(self.project), ".sentinel-hooks")


class FileBackendScenarios(Scenarios, unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = make_project(Path(temporary.name))
        self.offset = timedelta(0)
        start = work.utc_now()  # a frozen clock: the lease arithmetic is exact
        self.ledger = work.Ledger.open(self.project, clock=lambda: start + self.offset)

    def hand_close(self, gate: str) -> None:
        self.ledger.backend._gate_close(gate, "closed by hand")

    def test_the_file_backend_is_chosen_without_beads_and_writes_its_two_files(self) -> None:
        self.assertIsInstance(self.ledger.backend, work.FileBackend)
        self.ledger.create_graph(GRAPH)
        self.ledger.claim("a", NO_GATES)
        self.assertTrue((self.project / "hw/.cad/work/ledger.json").is_file())
        lease = json.loads((self.project / ".cad-work/a/status.json").read_text(encoding="utf-8"))
        self.assertEqual(lease["assignee"], "cad/a")
        self.assertLessEqual({"heartbeat_at", "lease_expires_at"}, set(lease))

    def test_a_void_that_fails_while_writing_leaves_the_ledger_as_it_was(self) -> None:
        self.void_setup()
        path = self.project / "hw" / ".cad" / "work" / "ledger.json"
        before = path.read_bytes()
        with mock.patch.object(work, "_atomic_write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(work.WorkError, "cannot write"):
                self.ledger.void_gate("g1", "g1.v2", reopen=["i"])
        self.assertEqual(path.read_bytes(), before)
        self.assert_nothing_retired()

    def test_heartbeat_pushes_the_lease_forward_by_the_clock(self) -> None:
        self.ledger.create_graph(GRAPH)
        before = self.ledger.claim("a", NO_GATES)
        self.advance(minutes=3)
        after = self.ledger.heartbeat("a")
        assert before.lease_expires_at and after.lease_expires_at
        self.assertEqual(
            work._parse(after.lease_expires_at) - work._parse(before.lease_expires_at), timedelta(minutes=3)
        )


@unittest.skipUnless(HAVE_BD, "needs bd >= 1.3.1 on PATH")
class RealBdScenarios(Scenarios, unittest.TestCase):
    template: Path
    template_root: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls.template_root = tempfile.TemporaryDirectory()
        cls.template = make_project(Path(cls.template_root.name) / "template")
        work.bd_init(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.template_root.cleanup()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        shutil.copytree(self.template, self.project, symlinks=True)
        self.offset = timedelta(0)
        self.ledger = work.Ledger.open(self.project, clock=lambda: work.utc_now() + self.offset)

    VOID_FAULTS = ("_gate_close",)

    def hand_close(self, gate: str) -> None:
        gate_id = next(g.id for g in self.ledger.gates() if g.name == gate)
        done = subprocess.run(
            ["bd", "close", gate_id, "--reason", "closed by hand", "--json"],
            cwd=self.project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_bd_backend_is_chosen_when_beads_exists(self) -> None:
        self.assertIsInstance(self.ledger.backend, work.BdBackend)

    def test_init_adds_no_hooks_and_no_agent_files(self) -> None:
        self.assertEqual(hooks_path(self.template), ".sentinel-hooks")
        for name in ("AGENTS.md", "CLAUDE.md", ".cursor", ".claude", ".codex", ".agents"):
            self.assertFalse((self.template / name).exists(), name)


# --- the bd adapter against recorded bd 1.3.1 output --------------------------------------------------------------

SHIM = """#!{python}
import json, os, sys, time
config = json.load(open(os.environ["FAKE_BD_CONFIG"]))
argv = sys.argv[1:]
null = os.path.samestat(os.fstat(0), os.stat(os.devnull))
with open(config["log"], "a") as log:
    log.write(json.dumps({{"argv": argv, "stdin_null": null}}) + "\\n")
if argv == ["--version"]:
    time.sleep(config.get("version_hang", 0))
    print(config["version"])
    sys.exit(0)

def match(pattern, got):
    if not pattern:
        return not got
    if pattern[0].startswith("<") and pattern[0].endswith(">"):
        return any(match(pattern[1:], got[n:]) for n in range(1, len(got) + 1))
    return bool(got) and pattern[0] == got[0] and match(pattern[1:], got[1:])

state = config["state"]
used = json.load(open(state)) if os.path.exists(state) else {{}}
for index, rule in enumerate(config["rules"]):
    recorded = json.load(open(os.path.join(config["fixtures"], rule["fixture"] + ".json")))
    if not match(rule.get("argv", recorded["argv"]), argv):
        continue
    if used.get(str(index), 0) >= rule.get("times", 1 << 30):
        continue
    used[str(index)] = used.get(str(index), 0) + 1
    json.dump(used, open(state, "w"))
    time.sleep(rule.get("hang", 0))
    sys.stdout.write(rule.get("stdout", recorded["stdout"]))
    sys.stderr.write(rule.get("stderr", recorded["stderr"]))
    sys.exit(rule.get("rc", recorded["rc"]))
sys.stderr.write("unscripted: " + json.dumps(argv))
sys.exit(99)
"""
SNAPSHOT = [{"fixture": "list_units"}, {"fixture": "list_gates", "stdout": "[]\n"}, {"fixture": "show_units"}]


def recorded(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def id_of(name: str) -> str:
    return json.loads(recorded(f"create_{name}")["stdout"])["id"]


class FakeTime:
    def __init__(self) -> None:
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now

    def backoff(self):
        return work.Backoff(sleep=self.sleep, monotonic=self.monotonic)


class FakeBdTests(unittest.TestCase):
    """A `bd` shim on PATH replays recorded output; the log shows what work.py sent."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        (self.project / ".beads").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shim = self.bin / "bd"
        shim.write_text(SHIM.format(python=sys.executable), encoding="utf-8")
        shim.chmod(0o755)
        self.time = FakeTime()
        self.log = self.root / "calls.log"
        patcher = mock.patch.dict(
            os.environ,
            {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_BD_CONFIG": str(self.root / "config.json")},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(
        self,
        rules: list[dict],
        version: str = "bd version 1.3.1 (fake)",
        bd_timeout: float = work.BD_TIMEOUT_S,
        version_hang: float = 0,
    ) -> "work.Ledger":
        (self.root / "config.json").write_text(
            json.dumps(
                {
                    "log": str(self.log),
                    "state": str(self.root / "state.json"),
                    "fixtures": str(FIXTURES),
                    "rules": rules,
                    "version_hang": version_hang,
                    "version": version,
                }
            ),
            encoding="utf-8",
        )
        return work.Ledger.open(self.project, make_backoff=self.time.backoff, bd_timeout=bd_timeout)

    def logged(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def calls(self) -> list[list[str]]:
        return [line["argv"] for line in self.logged() if line["argv"] != ["--version"]]

    def test_units_parse_labels_stage_and_dependencies(self) -> None:
        units = {u.unit: u for u in self.configure(SNAPSHOT).units()}
        self.assertEqual((units["A"].id, units["A"].stage, units["A"].status), (id_of("a"), "layout", "open"))
        self.assertEqual(units["B"].deps, ("A",))

    def test_graph_creation_sends_the_spec_labels_and_the_dependency(self) -> None:
        empty = {"fixture": "list_empty", "argv": ["list", "--label", "cad", "--all", "--limit", "0", "--json"]}
        ledger = self.configure(
            [
                empty,
                {"fixture": "list_gates", "stdout": "[]\n"},
                {"fixture": "create_a"},
                {"fixture": "create_b"},
                {"fixture": "dep_add"},
            ]
        )
        created = ledger.create_graph(
            [work.UnitSpec("A", "layout", "unit A"), work.UnitSpec("B", "layout", "unit B", ("A",))]
        )
        self.assertEqual(created, ["A", "B"])
        self.assertIn(
            ["create", "unit A", "-t", "task", "-l", "cad,cad:stage=layout,cad:unit=A", "--json"], self.calls()
        )
        self.assertIn(["dep", "add", id_of("b"), id_of("a"), "--json"], self.calls())

    def test_ready_and_blocked_use_bd_output(self) -> None:
        ledger = self.configure([{"fixture": "ready"}, {"fixture": "blocked"}, *SNAPSHOT])
        self.assertEqual(names(ledger.ready(NO_GATES)), ["A"])
        self.assertEqual([(u.unit, u.blocked_by) for u in ledger.blocked()], [("B", ("A",))])

    def test_claim_names_the_unit_actor_and_refuses_a_blocked_unit(self) -> None:
        ledger = self.configure(
            [
                {"fixture": "list_unit_a"},
                {"fixture": "list_unit_b"},
                {"fixture": "ready"},
                {"fixture": "claim_a"},
                *SNAPSHOT,
            ]
        )
        ledger.claim("A", NO_GATES)
        self.assertIn(["update", id_of("a"), "--claim", "--actor", "cad/A", "--json"], self.calls())
        before = len(self.calls())
        with self.assertRaises(work.ClaimError):
            ledger.claim("B", NO_GATES)
        self.assertFalse(any(call[0] == "update" for call in self.calls()[before:]))

    def test_claim_conflict_is_a_claim_error_and_is_not_retried(self) -> None:
        ledger = self.configure([{"fixture": "list_unit_a"}, {"fixture": "claim_conflict"}])
        with self.assertRaisesRegex(work.ClaimError, "already claimed by cad/A"):
            ledger.backend.claim("A", "cad/Z")
        self.assertEqual(self.time.sleeps, [])

    def test_heartbeat_always_names_the_actor(self) -> None:
        ledger = self.configure([{"fixture": "list_unit_a"}, {"fixture": "heartbeat_a"}])
        ledger.heartbeat("A")
        self.assertIn(["heartbeat", id_of("a"), "--actor", "cad/A", "--json"], self.calls())
        refused = self.configure(
            [
                {"fixture": "list_unit_a"},
                {"fixture": "heartbeat_no_actor", "argv": ["heartbeat", "<A>", "--actor", "cad/Z", "--json"]},
            ]
        )
        with self.assertRaisesRegex(work.ClaimError, "already claimed"):
            refused.heartbeat("A", "cad/Z")

    def test_unclaim_is_an_if_assignee_swap(self) -> None:
        ledger = self.configure([{"fixture": "list_unit_a"}, {"fixture": "unclaim_mismatch"}])
        with self.assertRaisesRegex(work.ClaimError, "assignee mismatch"):
            ledger.unclaim("A", "cad/Z", "release")

    def test_evidence_comment_and_close_pass_the_actor(self) -> None:
        ledger = self.configure(
            [{"fixture": "list_unit_a"}, {"fixture": "comment_add"}, {"fixture": "close_wrong_actor"}]
        )
        ledger.comment("A", "/abs/evidence.json sha256:" + "ab" * 32, "cad/A")
        with self.assertRaisesRegex(work.ClaimError, "cannot close"):
            ledger.close("A", "done", "cad/Z")

    UNCLAIM = {
        "fixture": "unclaim_ok",
        "argv": ["unclaim", "<A>", "--if-assignee", "cad/A", "--reason", "<R>", "--json"],
    }

    def test_reopen_clears_the_assignee_bd_keeps(self) -> None:
        ledger = self.configure([{"fixture": "list_unit_a_reopened"}, {"fixture": "reopen_a"}, self.UNCLAIM])
        ledger.reopen("A", "void")
        self.assertEqual(self.calls()[-1][:5], ["unclaim", id_of("a"), "--if-assignee", "cad/A", "--reason"])

    def test_a_retried_reopen_still_releases_the_assignee_it_finds(self) -> None:
        """The first reopen worked but the release failed; the retry's `reopen` is a no-op that prints nothing."""
        again = {"fixture": "reopen_open", "argv": ["reopen", "<A>", "--reason", "<R>", "--json"]}
        ledger = self.configure([{"fixture": "list_unit_a_reopened"}, again, self.UNCLAIM])
        ledger.reopen("A", "void")
        self.assertEqual(self.calls()[-1][:5], ["unclaim", id_of("a"), "--if-assignee", "cad/A", "--reason"])

    def test_reopen_never_releases_a_live_claim(self) -> None:
        again = {"fixture": "reopen_open", "argv": ["reopen", "<A>", "--reason", "<R>", "--json"]}
        ledger = self.configure([{"fixture": "list_unit_a_claimed"}, again, self.UNCLAIM])
        ledger.reopen("A", "void")
        self.assertNotIn("unclaim", [call[0] for call in self.calls()])

    def test_every_bd_call_runs_with_stdin_closed(self) -> None:
        ledger = self.configure([{"fixture": "list_unit_a"}, {"fixture": "ready"}, {"fixture": "claim_a"}, *SNAPSHOT])
        ledger.claim("A", NO_GATES)
        logged = self.logged()
        self.assertIn(["--version"], [line["argv"] for line in logged])
        self.assertGreaterEqual(len(logged), 4)
        self.assertTrue(all(line["stdin_null"] for line in logged), logged)

    def test_a_version_probe_that_never_answers_is_busy_or_unreachable(self) -> None:
        with self.assertRaisesRegex(work.BusyError, "bd --version did not answer within"):
            self.configure([], bd_timeout=2, version_hang=30)

    def test_bd_init_that_never_answers_is_busy_or_unreachable(self) -> None:
        self.configure([{"fixture": "ready", "argv": ["init", "--skip-hooks", "--skip-agents", "--quiet"], "hang": 30}])
        with self.assertRaisesRegex(work.BusyError, "bd init did not answer within"):
            work.bd_init(self.project, bd=str(self.bin / "bd"), timeout=2)
        self.assertTrue(self.logged()[-1]["stdin_null"])

    def test_gate_creation_labels_the_gate_with_its_name(self) -> None:
        ledger = self.configure(
            [
                {"fixture": "list_gate", "stdout": "[]\n"},
                {"fixture": "list_unit_b"},
                {"fixture": "gate_create"},
                {"fixture": "label_gate"},
            ]
        )
        self.assertTrue(ledger.create_gate("S.provisional", ["B"], "S.provisional"))
        gate_id = json.loads(recorded("gate_create")["stdout"])["id"]
        self.assertIn(["label", "add", gate_id, "cad:gate=S.provisional", "--json"], self.calls())

    def test_closed_gate_is_parsed_from_the_gate_list(self) -> None:
        ledger = self.configure(
            [{"fixture": "list_units"}, {"fixture": "list_gates_closed"}, {"fixture": "show_units"}]
        )
        self.assertEqual([(g.name, g.status, g.void) for g in ledger.gates()], [("S.provisional", "closed", False)])

    def test_waiter_is_added_with_the_given_actor(self) -> None:
        ledger = self.configure([{"fixture": "list_gate"}, {"fixture": "add_waiter"}])
        ledger.add_gate_waiter("S.provisional", "cad/B")
        last = self.calls()[-1]
        self.assertEqual([last[0], last[1], last[3]], ["gate", "add-waiter", "cad/B"])

    def test_a_busy_store_is_retried_with_growing_waits(self) -> None:
        busy = {"fixture": "ready", "rc": 1, "stdout": "", "stderr": "Error: database is locked\n", "times": 2}
        ledger = self.configure([busy, {"fixture": "ready"}, *SNAPSHOT])
        self.assertEqual(names(ledger.ready(NO_GATES)), ["A"])
        self.assertEqual(self.time.sleeps, [0.05, 0.1])

    def test_a_store_busy_for_the_whole_budget_raises_within_30_seconds(self) -> None:
        busy = {"fixture": "ready", "rc": 1, "stdout": "", "stderr": "database is locked\n"}
        ledger = self.configure([busy])
        with self.assertRaises(work.BusyError):
            ledger.backend.ready()
        self.assertLessEqual(sum(self.time.sleeps), 30.0)
        self.assertGreater(sum(self.time.sleeps), 25.0)

    def test_a_bd_that_never_answers_is_reported_busy_or_unreachable(self) -> None:
        ledger = self.configure([{"fixture": "ready", "hang": 30}], bd_timeout=2)
        with self.assertRaisesRegex(work.BusyError, "did not answer within .*busy or unreachable"):
            ledger.backend.ready()
        self.assertEqual(self.time.sleeps, [])  # a timeout is final, not a busy retry

    def test_bd_older_than_1_3_1_is_refused(self) -> None:
        with self.assertRaisesRegex(work.WorkError, "bd >= 1.3.1"):
            self.configure([], version="bd version 1.3.0 (fake)")

    def test_without_beads_no_bd_is_run(self) -> None:
        shutil.rmtree(self.project / ".beads")
        ledger = self.configure([])
        self.assertIsInstance(ledger.backend, work.FileBackend)
        ledger.create_graph(GRAPH)
        self.assertEqual(self.calls(), [])

    def test_the_opencode_plugin_lock_file_is_shared_when_present(self) -> None:
        self.assertEqual(self.configure([]).backend.lock_path.name, "cad-work.lock")
        (self.project / ".beads" / "omp-embedded-write.lock").touch()
        self.assertEqual(self.configure([]).backend.lock_path.name, "omp-embedded-write.lock")


class LockTests(unittest.TestCase):
    def test_a_lock_held_elsewhere_raises_busy_within_30_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "x.lock"
            fake = FakeTime()
            with path.open("a") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX)
                with self.assertRaises(work.BusyError), work.store_lock(path, fake.backoff):
                    pass
            self.assertLessEqual(sum(fake.sleeps), 30.0)
            with work.store_lock(path, fake.backoff):  # free again
                pass


WORKER = """
import importlib.util, json, os, sys, time
from pathlib import Path

spec = importlib.util.spec_from_file_location("cad_work_child", sys.argv[1])
work = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = work
spec.loader.exec_module(work)
mode, project, marker = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
ledger = work.Ledger.open(project)
if mode == "race-claim":
    actor = sys.argv[5]
    original = work.FileBackend._set_lease

    def slow(self, unit, lease):  # a slow disk: the race window stays open for half a second
        time.sleep(0.5)
        return original(self, unit, lease)

    work.FileBackend._set_lease = slow
    (marker / ("ready-" + actor.replace("/", "_"))).touch()
    while not (marker / "go").exists():
        time.sleep(0.005)
    try:
        ledger.claim("a", frozenset(), actor)
        print(json.dumps({"won": True}))
    except work.ClaimError as error:
        print(json.dumps({"won": False, "error": str(error)}))
else:
    os.replace = lambda *args: os._exit(9)  # die between writing the temporary file and renaming it
    if mode == "die-in-graph":
        ledger.create_graph([work.UnitSpec("b", "layout")])
    else:
        ledger.heartbeat("a", "cad/a")
"""


def child(mode: str, project: Path, marker: Path, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, str(SCRIPT), mode, str(project), str(marker), *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class FileBackendConcurrencyTests(unittest.TestCase):
    """The file backend's public operations under a race and under an interrupted write."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        self.marker = Path(temporary.name) / "marker"
        self.marker.mkdir()
        self.project.mkdir()
        self.ledger = work.Ledger.open(self.project)
        self.ledger.create_graph([work.UnitSpec("a", "layout")])
        self.ledger_path = self.project / "hw" / ".cad" / "work" / "ledger.json"
        self.lease_path = self.project / ".cad-work" / "a" / "status.json"

    def test_two_processes_racing_to_claim_one_unit_leave_exactly_one_winner(self) -> None:
        actors = ("cad/x", "cad/y")
        racers = [child("race-claim", self.project, self.marker, actor) for actor in actors]
        deadline = time.monotonic() + 30
        while len(list(self.marker.glob("ready-*"))) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        (self.marker / "go").touch()
        results = []
        for racer in racers:
            out, err = racer.communicate(timeout=60)
            self.assertEqual(racer.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(sorted(result["won"] for result in results), [False, True], results)
        winner = actors[[result["won"] for result in results].index(True)]
        self.assertEqual(json.loads(self.lease_path.read_text(encoding="utf-8"))["assignee"], winner)
        loser = next(result for result in results if not result["won"])
        self.assertIn("already claimed by", loser["error"])

    def test_a_write_interrupted_before_the_rename_leaves_the_old_state_readable(self) -> None:
        self.ledger.claim("a", NO_GATES)
        for mode, path in (("die-in-graph", self.ledger_path), ("die-in-heartbeat", self.lease_path)):
            with self.subTest(mode):
                before = path.read_bytes()
                dying = child(mode, self.project, self.marker)
                _, err = dying.communicate(timeout=60)
                self.assertEqual(dying.returncode, 9, err)
                self.assertEqual(path.read_bytes(), before)
                json.loads(path.read_bytes())  # still parses
                self.assertEqual(names(work.Ledger.open(self.project).units()), ["a"])
        # the dead writers left their temporary files and no lock: the next writer is not blocked
        self.ledger.create_graph([work.UnitSpec("b", "layout")])
        self.assertEqual(names(self.ledger.units()), ["a", "b"])

    def test_a_write_that_raises_midway_leaves_the_old_state_and_no_temporary_file(self) -> None:
        before = self.ledger_path.read_bytes()

        def partial(data, stream, **kwargs):
            stream.write('{"schema_ver')
            raise RuntimeError("disk full")

        with mock.patch.object(work.json, "dump", side_effect=partial):
            with self.assertRaises(RuntimeError):
                self.ledger.create_graph([work.UnitSpec("b", "layout")])
        self.assertEqual(self.ledger_path.read_bytes(), before)
        self.assertEqual(list(self.ledger_path.parent.glob(".ledger.json.*.tmp")), [])
        self.assertEqual(names(self.ledger.units()), ["a"])


class ScratchLeaseTests(unittest.TestCase):
    """The file backend's lease sits in .cad-work/<unit>/status.json, where scratch.py also works."""

    def scratch(self, project: Path, *args: str) -> tuple[int, dict]:
        done = subprocess.run(
            [sys.executable, str(SCRATCH), *args], cwd=project, capture_output=True, text=True, timeout=120
        )
        try:
            return done.returncode, json.loads(done.stdout)
        except ValueError:
            self.fail(f"no JSON on stdout (exit {done.returncode}):\n{done.stdout}\n{done.stderr[-2000:]}")

    def test_a_claim_survives_scratch_init_reset_and_a_repeated_init(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            shutil.copytree(TEMPLATE, project / "hw")
            ledger = work.Ledger.open(project)
            ledger.create_graph([work.UnitSpec("part-a", "parts")])
            ledger.claim("part-a", NO_GATES)
            status = project / ".cad-work" / "part-a" / "status.json"
            lease = status.read_text(encoding="utf-8")
            for extra, expected in (([], "created"), ([], "exists"), (["--reset"], "created")):
                with self.subTest(extra=extra, expected=expected):
                    args = ["init", "--hw", "hw", "--unit", "part-a", "--part", "PRT-0001", *extra]
                    code, report = self.scratch(project, *args)
                    self.assertEqual((code, report["status"]), (0, expected), report)
                    self.assertEqual(status.read_text(encoding="utf-8"), lease)
            held = next(item for item in ledger.units() if item.unit == "part-a")
            self.assertEqual((held.status, held.assignee), ("in_progress", "cad/part-a"))
            self.assertEqual(ledger.heartbeat("part-a").assignee, "cad/part-a")
            self.assertTrue((project / ".cad-work" / "part-a" / "baseline.json").is_file())


class FlowGatesTests(unittest.TestCase):
    """The accepted-gate set the CLI hands to the guard: flow.gates of hw/product.yaml, read only.

    The 01 schema: `gates` maps a gated stage to {state: provisional|approved, version, provisional, approved};
    a stage with no entry is `unrecorded`. Both recorded states bind: `provisional` binds the stage's provisional
    gate, `approved` the provisional gate and the approval gate of a two-step stage (it has a `provisional`
    decision) or the one gate of a single-gate stage.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / "hw").mkdir()

    def product(self, text: str) -> None:
        (self.project / "hw" / "product.yaml").write_text(text, encoding="utf-8")

    def test_no_file_no_flow_no_gates_or_an_empty_mapping_means_nothing_is_approved_yet(self) -> None:
        self.assertEqual(work.flow_gates_from_product(self.project), frozenset())
        for text in ("id: p\n", "flow: {stage: ground}\n", "flow: {gates: {}}\n", "flow: {gates: null}\n", ""):
            with self.subTest(text):
                self.product(text)
                self.assertEqual(work.flow_gates_from_product(self.project), frozenset())

    def test_provisional_and_approved_entries_bind_the_gates_of_their_stage(self) -> None:
        cases = {
            "provisional": ("layout: {state: provisional, version: v2, provisional: DR-003}", {"layout.provisional"}),
            "approved two-step": (
                "analysis: {state: approved, version: v2, provisional: DR-003, approved: DR-004}",
                {"analysis.provisional", "analysis.approval"},
            ),
            "intent": ("intent: {state: approved, version: v1, approved: DR-001}", {"intent"}),
            "concept is the variant gate": ("concept: {state: approved, version: v1, approved: DR-002}", {"variant"}),
            "design_freeze is the freeze gate": (
                "design_freeze: {state: approved, version: v3, approved: DR-009}",
                {"freeze"},
            ),
            "release": ("release: {state: approved, version: v3, approved: DR-010}", {"release"}),
            "several stages": (
                "intent: {state: approved, version: v1, approved: DR-001}\n"
                "    parts: {state: provisional, version: v2}",
                {"intent", "parts.provisional"},
            ),
        }
        for label, (entries, expected) in cases.items():
            with self.subTest(label):
                self.product(f"flow:\n  gates:\n    {entries}\n")
                self.assertEqual(work.flow_gates_from_product(self.project), frozenset(expected))

    def test_the_gated_stages_are_stages_of_the_product_schema(self) -> None:
        schema = json.loads((HERE.parents[1] / "cad-record" / "schemas" / "product.schema.json").read_text("utf-8"))
        stages = set(schema["$defs"]["stage"]["enum"])
        self.assertLessEqual(set(work.SINGLE_GATES) | set(work.TWO_STEP_STAGES), stages)
        self.assertEqual(set(work.SINGLE_GATES.values()), {"intent", "variant", "freeze", "release"})

    def test_every_other_shape_of_flow_gates_fails_closed_with_the_problem_named(self) -> None:
        cases = {
            "a list": (
                "flow: {gates: [layout.provisional]}",
                "flow.gates must be a mapping from stage to entry, not a list",
            ),
            "a list of entries": ("flow: {gates: [{gate: layout}]}", "not a list"),
            "a scalar": ("flow: {gates: yes}", "flow.gates must be a mapping from stage to entry, not a bool"),
            "a string": ("flow: {gates: approved}", "not a str"),
            "a bare state": ("flow: {gates: {layout: approved}}", "flow.gates.layout must be a mapping, not a str"),
            "a list entry": ("flow: {gates: {layout: [approved]}}", "flow.gates.layout must be a mapping, not a list"),
            "a null entry": ("flow: {gates: {layout: null}}", "flow.gates.layout must be a mapping, not a NoneType"),
            "no state": ("flow: {gates: {layout: {version: v1}}}", "flow.gates.layout has no state"),
            "state open": (
                "flow: {gates: {layout: {state: open}}}",
                "flow.gates.layout.state must be provisional or approved, not 'open'",
            ),
            "state unrecorded": ("flow: {gates: {layout: {state: unrecorded}}}", "not 'unrecorded'"),
            "state case": ("flow: {gates: {layout: {state: Approved}}}", "not 'Approved'"),
            "state number": ("flow: {gates: {layout: {state: 3}}}", "not 3"),
            "state null": ("flow: {gates: {layout: {state: null}}}", "not None"),
            "a number as stage": (
                "flow: {gates: {3: {state: approved}}}",
                "flow.gates has a stage key that is not a name: 3",
            ),
            "a ledger gate name": (
                "flow: {gates: {layout.provisional.v2: {state: approved}}}",
                "flow.gates.layout.provisional.v2 is not a gated stage",
            ),
            "a stage with a space": ('flow: {gates: {"bad stage": {state: approved}}}', "is not a gated stage"),
            "an ungated stage": (
                "flow: {gates: {ground: {state: approved}}}",
                "flow.gates.ground is not a gated stage",
            ),
            "another ungated stage": ("flow: {gates: {bom: {state: approved}}}", "flow.gates.bom is not a gated stage"),
            "a stage that does not exist": ("flow: {gates: {frozen: {state: approved}}}", "is not a gated stage"),
            "a gate name for a stage": (
                "flow: {gates: {variant: {state: approved}}}",
                "flow.gates.variant is not a gated stage",
            ),
            "a provisional single gate": (
                "flow: {gates: {intent: {state: provisional}}}",
                "flow.gates.intent is a single-gate stage; its state can only be approved",
            ),
        }
        for label, (text, message) in cases.items():
            with self.subTest(label):
                self.product(text + "\n")
                with self.assertRaisesRegex(work.WorkError, re.escape(message)):
                    work.flow_gates_from_product(self.project)

    def test_the_cli_refuses_a_flow_gates_key_that_is_not_a_gated_stage(self) -> None:
        self.product("flow:\n  gates:\n    layout.provisional.v2: {state: approved, version: v1}\n")
        for command in (("ready",), ("claim", "b"), ("findings",)):
            with self.subTest(command):
                code, error = self.cli(*command)
                self.assertEqual(code, 1)
                self.assertIn("is not a gated stage", error["error"])

    def test_a_product_file_that_does_not_parse_is_an_error_not_an_empty_set(self) -> None:
        self.product("flow: [unclosed\n")
        with self.assertRaisesRegex(work.WorkError, "cannot read"):
            work.flow_gates_from_product(self.project)

    def cli(self, *argv: str) -> tuple[int, object]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = work.main(["--project", str(self.project), *argv])
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_cli_ready_and_claim_hold_a_hand_closed_gate_until_flow_gates_records_its_state(self) -> None:
        ledger = work.Ledger.open(self.project)
        ledger.create_graph([work.UnitSpec("b", "layout")])
        ledger.create_gate("layout.provisional", ["b"])
        ledger.backend._gate_close("layout.provisional", "closed by hand")
        self.assertEqual(self.cli("ready"), (0, []))
        code, error = self.cli("claim", "b")
        self.assertEqual(code, 1)
        self.assertIn("not ready", error["error"])
        self.assertEqual([f["code"] for f in self.cli("findings")[1]], [work.GATE_UNBOUND])
        self.product("flow:\n  gates:\n    layout: {state: provisional, version: v1, provisional: DR-001}\n")
        self.assertEqual([u["unit"] for u in self.cli("ready")[1]], ["b"])
        self.assertEqual(self.cli("claim", "b")[1]["assignee"], "cad/b")
        self.assertEqual(self.cli("findings"), (0, []))

    def test_an_approval_gate_closed_while_flow_gates_says_provisional_is_unbound(self) -> None:
        ledger = work.Ledger.open(self.project)
        ledger.create_graph([work.UnitSpec("n", "analysis")])
        ledger.create_gate("layout.provisional", ["n"])
        ledger.create_gate("layout.approval", ["n"])
        ledger.backend._gate_close("layout.provisional", "DR-001")
        ledger.backend._gate_close("layout.approval", "closed by hand")
        self.product("flow:\n  gates:\n    layout: {state: provisional, version: v1, provisional: DR-001}\n")
        self.assertEqual(
            [f.gate for f in ledger.findings(work.flow_gates_from_product(self.project))], ["layout.approval"]
        )
        self.assertEqual(self.cli("ready"), (0, []))


class SurfaceTests(unittest.TestCase):
    def cli(self, project: Path, *argv: str):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(work.main(["--project", str(project), *argv]), 0)
        return json.loads(out.getvalue())

    def test_the_cli_offers_no_way_to_resolve_a_gate(self) -> None:
        for command in ("gate-resolve", "resolve", "gate-close"):
            with (
                self.subTest(command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                work.main(["--project", ".", command, "g1"])
            self.assertEqual(raised.exception.code, 2)

    def test_resolve_gate_needs_an_accepted_decision(self) -> None:
        with self.assertRaises(ValueError):
            work.AcceptedDecision("DR-001", 1, "ab" * 32, status="proposed")
        with self.assertRaises(ValueError):
            work.AcceptedDecision("DR-1", 1, "ab" * 32)
        with self.assertRaises(ValueError):
            work.AcceptedDecision("DR-001", 1, "short")
        with tempfile.TemporaryDirectory() as temporary:
            ledger = work.Ledger.open(Path(temporary))
            ledger.create_graph([work.UnitSpec("b", "layout")])
            ledger.create_gate("g1", ["b"])
            with self.assertRaises(TypeError):
                work.resolve_gate(ledger, "g1", "accepted")  # type: ignore[arg-type]
            self.assertEqual(next(g for g in ledger.gates() if g.name == "g1").status, "open")

    def test_the_cli_runs_the_dispatch_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "graph.json").write_text(
                json.dumps([{"unit": "a", "stage": "layout"}, {"unit": "b", "stage": "layout", "deps": ["a"]}]),
                encoding="utf-8",
            )
            self.assertEqual(self.cli(project, "graph", "--file", str(project / "graph.json")), {"created": ["a", "b"]})
            self.assertEqual([u["unit"] for u in self.cli(project, "ready")], ["a"])
            self.assertEqual(self.cli(project, "claim", "a")["assignee"], "cad/a")
            self.cli(project, "close", "a")
            self.assertEqual([u["unit"] for u in self.cli(project, "ready")], ["b"])
            self.assertEqual(self.cli(project, "findings"), [])

    def test_a_refusal_prints_an_error_and_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(work.main(["--project", temporary, "claim", "nope"]), 1)
        self.assertIn("no unit", json.loads(err.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
