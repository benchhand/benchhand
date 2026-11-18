#!/usr/bin/env python3
"""FEA load cases that name the stop reaction they come from (fea_reaction.py, #153).

A load with `reaction: IF-###` is checked against the sealed stop-reaction bundle of the version before anything is
solved. These tests publish real bundles through cad-record's check_packet and layout and drive fea_static.main with
the solve stubbed to fail if it is ever reached. Kernel-free; test_fea_static.py has the FreeCAD and ccx cases.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

import fea_reaction  # noqa: E402
import fea_static  # noqa: E402


def load_by_path(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAYOUT = load_by_path("cad_record_layout", SKILLS / "cad-record" / "scripts" / "layout.py")
CHECK_PACKET = load_by_path("cad_record_check_packet", SKILLS / "cad-record" / "scripts" / "check_packet.py")
INTENT = load_by_path("cad_record_manufacturing_intent", SKILLS / "cad-record" / "scripts" / "manufacturing_intent.py")

CASE_TAIL = """material: {name: Aluminium 6061, E: 69000, nu: 0.33, density: 2700, yield: 276}
fixed_faces: {plane: {axis: y, value: 50, tolerance: 0.000001}}
loads:
  - selector: {plane: {axis: y, value: 150, tolerance: 0.000001}}
    force: [0, 0, -50]
    reaction: IF-006
mesh_size: 5
"""
INTERFACE = "IF-006"


def end(status: str, design: float | None) -> dict[str, Any]:
    return {"touching": True, "reaction": {"status": status, "design_reaction_n": design}}


def packet(outcome: str) -> dict[str, Any]:
    """A schema-valid stop-reaction packet for IF-006 with one outcome."""
    obligation = f"engineering.stop_reaction@{INTERFACE}"
    row: dict[str, Any] = {"obligation": obligation, "outcome": outcome, "level": "simulation_supported",
                           "method": "rigid statics of the modelled stop contact", "coverage": "one pivot at its limits"}
    if outcome == "unknown":
        row["uncertainty"] = "the reaction is not established"
    return {"schema_version": 1, "record_version": 2, "producer": "cad-analysis stop_reaction.py",
            "scope": [{"interface": INTERFACE}],
            "expected": [{"id": obligation, "family": "engineering.stop_reaction", "subject": {"interface": INTERFACE},
                          "level": "simulation_supported", "stage": "layout", "basis": "test"}],
            "outcomes": [row], "findings": [], "dependencies": {}, "complete": True,
            "result": "pass" if outcome == "not_applicable" else outcome}


def entry(outcome: str = "pass", ends: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"outcome": outcome, "ends": ends or {}}
    if reason:
        data["reason"] = reason
    return data


class Scenario:
    """A project with the sealed version v1, a case that names IF-006 for its 50 N load and, optionally, a stop-reaction bundle."""

    def __init__(self, test: unittest.TestCase) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.test = test
        self.solves = 0
        self.project = Path(temporary.name) / "project"
        self.hw = self.project / "hw"
        self.case_dir = self.hw / "analysis" / "cases" / "cantilever"
        self.case_dir.mkdir(parents=True)
        (self.case_dir / "beam.step").write_bytes(b"STEP geometry")
        self.case = self.case_dir / "case.yaml"
        self.case.write_text("step: beam.step\n" + CASE_TAIL, encoding="utf-8")
        self.version = self.hw / "versions" / "v1"
        record = self.version / "record"
        record.mkdir(parents=True)
        for name in ("product.yaml", "interfaces.yaml", "assembly.yaml"):
            (record / name).write_text("{}\n", encoding="utf-8")
        self.seal()

    def seal(self) -> None:
        files = {path.relative_to(self.version).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.version.rglob("*") if path.is_file() and path.name != "manifest.json"}
        (self.version / "manifest.json").write_text(json.dumps({"label": "v1", "files": files}), encoding="utf-8")

    def publish(self, interfaces: dict[str, Any], outcome: str = "pass", replace: bool = False) -> Path:
        detail = {"interfaces": interfaces, "limits": []}
        recipe = {"detail": hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()}
        output, _ = CHECK_PACKET.emit(LAYOUT, self.hw, "v1", fea_reaction.SUBJECT, "cad-analysis stop_reaction.py", packet(outcome),
                                      detail, fea_reaction.EVIDENCE, recipe, out=None, replace=replace,
                                      record_version=INTENT.RECORD_VERSION)
        return Path(output["bundle"])

    def call(self, replace: bool = False, solve: bool = True) -> tuple[int, dict[str, Any]]:
        """fea_static.main on the case with a stub for the solver: it passes, or it fails the test when `solve` is False."""
        argv = [str(self.case), "--hw", str(self.hw), "--version", "v1"] + (["--replace"] if replace else [])
        tools = {"freecad": {"path": "/opt/freecad/freecadcmd", "version": "1.1.4"},
                 "gmsh": {"path": "/opt/freecad/gmsh", "version": "4.15.0"},
                 "calculix": {"path": "/opt/freecad/ccx", "version": "2.23"}}
        refusal = AssertionError("the case was refused before the solve, yet a solve started")
        probe = {"return_value": tools} if solve else {"side_effect": AssertionError("the solvers were probed")}
        output = io.StringIO()
        with patch("fea_static.find_freecad", return_value=Path("/fake/freecadcmd")), \
             patch("fea_static.solver_versions", **probe), \
             patch("fea_static.run_job", side_effect=self.passing_job if solve else refusal), patch("sys.stdout", output):
            status = fea_static.main(argv)
        return status, json.loads(output.getvalue())

    def passing_job(self, case_path: Path, case: dict, **kwargs: Any) -> tuple[dict, int]:
        self.solves += 1
        run_dir = Path(kwargs["scratch_root"]) / "jobs" / "fea_static" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "solver.dat").write_text("solver output\n", encoding="utf-8")
        result = {"status": "pass", "run_directory": str(run_dir), "retained_files": ["solver.dat"],
                  "solver_versions": kwargs["solver_versions"]}
        kwargs["result_path"].write_text(json.dumps(result), encoding="utf-8")
        return result, 0

    def refused(self, code: str) -> dict[str, Any]:
        status, report = self.call(solve=False)
        self.test.assertEqual((status, report["error"]["code"]), (2, code), report)
        return report


class DesignReaction(unittest.TestCase):
    def test_a_load_below_the_design_reaction_is_refused_before_the_solve(self) -> None:
        scenario = Scenario(self)
        scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 343.1), "max": end("bounded", 120.0)})})
        report = scenario.refused("reaction_below_design")
        self.assertIn("50 N", report["error"]["message"])
        self.assertIn("343.1 N", report["error"]["message"])

    def test_a_load_at_or_above_the_design_reaction_proceeds_to_the_solve(self) -> None:
        for design in (50.0, 49.9999999999, 40.0):
            with self.subTest(design=design):
                scenario = Scenario(self)
                scenario.publish({INTERFACE: entry(ends={"min": end("bounded", design)})})
                status, report = scenario.call()
                self.assertEqual((status, report["status"]), (0, "pass"), report)

    def test_the_larger_of_two_stops_governs(self) -> None:
        scenario = Scenario(self)
        scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 30.0), "max": end("understated", 60.0)})})
        scenario.refused("reaction_below_design")

    def test_a_load_that_names_no_reaction_is_not_checked(self) -> None:
        scenario = Scenario(self)
        scenario.case.write_text(scenario.case.read_text(encoding="utf-8").replace("    reaction: IF-006\n", ""), encoding="utf-8")
        status, report = scenario.call()
        self.assertEqual((status, report["status"]), (0, "pass"), report)
        manifest = json.loads((scenario.hw / "analysis" / "v1" / "cantilever" / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("reactions", manifest["recipe"], "a case without a reaction load keeps its recipe's shape")


class RefusedReactions(unittest.TestCase):
    def test_no_bundle_is_missing(self) -> None:
        report = Scenario(self).refused("reaction_missing")
        self.assertIn("stop_reaction.py", report["error"]["message"])

    def test_a_bundle_with_no_assessment_of_the_interface_is_missing(self) -> None:
        scenario = Scenario(self)
        scenario.publish({"IF-001": entry(ends={"min": end("bounded", 400.0)})})
        scenario.refused("reaction_missing")

    def test_a_bundle_of_another_build_of_the_version_is_stale(self) -> None:
        scenario = Scenario(self)
        scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 40.0)})})
        manifest = json.loads((scenario.version / "manifest.json").read_text(encoding="utf-8"))
        (scenario.version / "manifest.json").write_text(json.dumps({**manifest, "rebuilt": True}), encoding="utf-8")
        report = scenario.refused("reaction_stale")
        self.assertIn("another build", report["error"]["message"])

    def test_a_bundle_that_fails_its_seal_is_stale(self) -> None:
        scenario = Scenario(self)
        bundle = scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 40.0)})})
        (bundle / "evidence" / fea_reaction.EVIDENCE).write_text('{"interfaces": {}}', encoding="utf-8")
        report = scenario.refused("reaction_stale")
        self.assertIn("seal", report["error"]["message"])

    def test_an_unbounded_stop_is_refused_even_when_the_other_stop_is_bounded(self) -> None:
        scenario = Scenario(self)
        scenario.publish({INTERFACE: entry("unknown", {"min": end("bounded", 40.0), "max": end("unbounded", None)})}, outcome="unknown")
        report = scenario.refused("reaction_unbounded")
        self.assertIn("max", report["error"]["message"])
        self.assertIn("design record", report["error"]["message"])

    def test_an_unknown_or_not_applicable_reaction_is_unknown(self) -> None:
        for outcome, reason in (("unknown", "the parts overlap by 0.18 mm at the max limit: a design defect, not a stop"),
                                ("not_applicable", "no movable joint connects the two parts")):
            with self.subTest(outcome=outcome):
                scenario = Scenario(self)
                scenario.publish({INTERFACE: entry(outcome, reason=reason)}, outcome=outcome)
                report = scenario.refused("reaction_unknown")
                self.assertIn(reason, report["error"]["message"])

    def test_a_standalone_case_cannot_name_a_reaction(self) -> None:
        scenario = Scenario(self)
        output = io.StringIO()
        with patch("fea_static.find_freecad", return_value=Path("/fake/freecadcmd")), \
             patch("fea_static.run_job", side_effect=AssertionError("refused before the solve")), patch("sys.stdout", output):
            status = fea_static.main([str(scenario.case)])
        self.assertEqual((status, json.loads(output.getvalue())["error"]["code"]), (2, "reaction_needs_version"))

    def test_a_reaction_must_name_an_interface(self) -> None:
        for bad in ("stop", "IF-6", "if-006", "IF-006 "):
            with self.subTest(reaction=bad):
                scenario = Scenario(self)
                scenario.case.write_text(scenario.case.read_text(encoding="utf-8").replace("reaction: IF-006", f"reaction: '{bad}'"),
                                         encoding="utf-8")
                self.assertIn("reaction", scenario.refused("invalid_case")["error"]["message"])


class RecipeCoversTheBundle(unittest.TestCase):
    def solved(self) -> Scenario:
        scenario = Scenario(self)
        scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 40.0)})})
        self.assertEqual(scenario.call()[0], 0)
        return scenario

    def test_the_recipe_names_the_bundle_it_read(self) -> None:
        scenario = self.solved()
        manifest = json.loads((scenario.hw / "analysis" / "v1" / "cantilever" / "manifest.json").read_text(encoding="utf-8"))
        basis = manifest["recipe"]["reactions"][INTERFACE]
        bundle = scenario.hw / "checks" / "v1" / "stop-reaction"
        self.assertEqual(basis, {"bundle": "hw/checks/v1/stop-reaction", "design_reaction_n": 40.0,
                                 "manifest_sha256": hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()})

    def test_an_unchanged_bundle_reuses_the_fea_result(self) -> None:
        scenario = self.solved()
        status, report = scenario.call()
        self.assertEqual((status, report["reused"], scenario.solves), (0, True, 1), "one solve in all, the first")

    def test_a_new_stop_reaction_result_needs_a_new_fea_run(self) -> None:
        scenario = self.solved()
        scenario.publish({INTERFACE: entry(ends={"min": end("bounded", 45.0)})}, replace=True)
        status, report = scenario.call()
        self.assertEqual(status, 2)
        self.assertIn("--replace", report["error"]["message"])
        status, report = scenario.call(replace=True)
        self.assertEqual((status, report["reused"]), (0, False))
        manifest = json.loads((scenario.hw / "analysis" / "v1" / "cantilever" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["recipe"]["reactions"][INTERFACE]["design_reaction_n"], 45.0)


class BundleNameMatchesTheProducer(unittest.TestCase):
    def test_the_subject_and_evidence_names_are_the_producers(self) -> None:
        source = (SCRIPTS / "stop_reaction.py").read_text(encoding="utf-8")
        self.assertIn(f'SUBJECT = "{fea_reaction.SUBJECT}"', source)
        self.assertIn(f'"{fea_reaction.EVIDENCE}"', source)


if __name__ == "__main__":
    unittest.main()
