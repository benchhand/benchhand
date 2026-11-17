#!/usr/bin/env python3
"""evidence_paths.py and its acceptance points: evidence in the record cites files the record holds.

An accepted ledger entry whose structured paths or free text name scratch (`.cad-work/`, `hw/.cad/`), a
temp directory, the home directory or anything outside the project root, or a record result its artifacts
do not list, is refused at the assessment, in the validate ledger findings and in the acceptance inventory.
The fixture mirrors EV-002 and EV-003 of the small-mechanism bench fixture that cited its measurement
script and result under .cad-work/.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

acceptance = load("cad_record_acceptance_fixtures", HERE / "fixtures" / "acceptance.py")
fx, mi, evidence, impact = acceptance.fx, acceptance.mi, acceptance.evidence, acceptance.impact
paths_module = load("cad_record_evidence_paths", HERE.parent / "scripts" / "evidence_paths.py")

TODAY = date(2026, 6, 1)
V1 = "a" * 64
SUBJECT = {"product": True, "requirements": ["REQ-001"]}
SIMULATION = "requirement.simulation_supported@REQ-001"
EVIDENCE_DIR = "hw/checks/v1/lever/evidence"
RESULT = f"{EVIDENCE_DIR}/regional-v1.json"
SCRIPT = f"{EVIDENCE_DIR}/regional_v1.py"
VETOED_METHOD = ("BRepExtrema distance from the sealed v1 lever STEP to each bracket STEP face, J-001 0 to 75 deg "
                 "(lead script .cad-work/layout/regional_v1.py, result .cad-work/layout/regional-v1.json): 0.300 mm.")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


class Project(unittest.TestCase):
    """A git project with the TST record. Its hw/checks/v1/lever/evidence folder holds the promoted measurement."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-evidence-paths-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.hw = fx.write_record(self.root / "hw", fx.tst_record())
        (self.root / "notes").mkdir()
        (self.root / "notes" / "method.md").write_text("tracked note\n", encoding="utf-8")
        (self.root / "notes" / "untracked.md").write_text("untracked note\n", encoding="utf-8")
        (self.root / ".cad-work" / "layout").mkdir(parents=True)
        (self.root / ".cad-work" / "layout" / "regional_v1.py").write_text("print()\n", encoding="utf-8")
        (self.hw / ".cad").mkdir()
        (self.hw / ".cad" / "builds.json").write_text("{}\n", encoding="utf-8")
        evidence_dir = self.root / EVIDENCE_DIR
        evidence_dir.mkdir(parents=True)
        (self.root / RESULT).write_text('{"min_mm": 0.3}\n', encoding="utf-8")
        (self.root / SCRIPT).write_text("print()\n", encoding="utf-8")
        sealed = self.hw / "versions" / "v3"
        sealed.mkdir(parents=True)
        (sealed / "assembly.step").write_text("ISO-10303-21;\n", encoding="utf-8")
        (self.hw / "versions" / "v3.partial").mkdir()
        (self.hw / "versions" / "v3.partial" / "assembly.step").write_text("ISO-10303-21;\n", encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "add", "hw/product.yaml", "hw/verification.csv", "notes/method.md")
        git(self.root, "add", "-f", "hw/.cad/builds.json")  # tracked on purpose: only the scratch rule may refuse it
        git(self.root, "commit", "-q", "-m", "fixture")
        self.record = impact.read_record(self.hw)
        self.paths = evidence.project_paths(self.hw)

    def entry(self, ident: str = "EV-001", *, artifacts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
        return acceptance.entry(self.record, ident, SUBJECT, "simulation_supported", requirement="REQ-001", version="v1",
                                manifest_sha256=V1, artifacts=artifacts or ["hw/verification.csv"], hw=self.hw, **extra)

    def refusal(self, entry: dict[str, Any]) -> list[Any]:
        return paths_module.entry_problems(self.paths, entry)

    def assessed(self, entry: dict[str, Any]) -> Any:
        record = mi.normalize_record({**{rel: doc for rel, doc in fx.tst_record().items() if not rel.startswith("_")},
                                      "manufacturing/evidence.yaml": {"entries": [entry]}})
        return evidence.assess(record, version="v1", manifest_sha256=V1, today=TODAY, paths=self.paths,
                               approved=lambda decision: True)


class VetoedFixture(Project):
    def test_scratch_script_and_result_in_method_are_refused_naming_the_path(self) -> None:
        entry = self.entry(method=VETOED_METHOD)
        problems = self.refusal(entry)
        self.assertEqual({p.code for p in problems}, {"evidence.scratch_path"})
        message = " ".join(p.message for p in problems)
        self.assertIn(".cad-work/layout/regional_v1.py", message)
        self.assertIn(".cad-work/layout/regional-v1.json", message)
        self.assertIn("hw/checks", message)

    def test_the_assessment_refuses_the_entry_and_does_not_count_it(self) -> None:
        result = self.assessed(self.entry(method=VETOED_METHOD))
        finding = next(f for f in result.findings if f.code == "evidence.scratch_path")
        self.assertEqual(finding.severity, "error")
        self.assertIn(".cad-work/layout/regional_v1.py", finding.message)
        self.assertEqual(result.statuses[SIMULATION].result, "unknown")

    def test_the_same_entry_with_the_result_promoted_is_accepted(self) -> None:
        method = f"BRepExtrema distance, J-001 0 to 75 deg (lead script {SCRIPT}, result {RESULT}): 0.300 mm."
        entry = self.entry(method=method, artifacts=["hw/verification.csv", SCRIPT, RESULT])
        self.assertEqual(self.refusal(entry), [])
        result = self.assessed(entry)
        self.assertNotIn("evidence.scratch_path", {f.code for f in result.findings})
        self.assertNotIn("evidence.uncited_artifact", {f.code for f in result.findings})
        self.assertEqual(result.statuses[SIMULATION].result, "pass")

    def test_a_promoted_result_the_text_cites_but_the_artifacts_omit_is_refused(self) -> None:
        entry = self.entry(method=f"result {RESULT}: 0.300 mm")
        problems = self.refusal(entry)
        self.assertEqual([p.code for p in problems], ["evidence.uncited_artifact"])
        self.assertIn(RESULT, problems[0].message)


class OutsideTheRecord(Project):
    def test_each_location_outside_the_record_is_refused(self) -> None:
        cases = {
            "dotdot escape": "see ../outside/result.json",
            "absolute outside": "see /etc/hosts.json",
            "home": "see ~/measurements/result.csv",
            "tmp": "see /tmp/result.json",
            "var folders": "see /var/folders/ab/cd/result.json",
            "hw/.cad scratch": "see hw/.cad/builds.json",
            "dotdot into scratch": "see hw/../.cad-work/layout/regional_v1.py",
            "untracked file in a git project": "see notes/untracked.md",
            "unsealed version": "see hw/versions/v3.partial/assembly.step",
            "missing file": "see notes/absent.md",
        }
        for name, method in cases.items():
            with self.subTest(name):
                problems = self.refusal(self.entry(method=method))
                self.assertEqual([p.code for p in problems], ["evidence.scratch_path"], method)
                self.assertIn(method.split()[-1], problems[0].message)

    def test_a_structured_artifact_in_scratch_is_refused(self) -> None:
        entry = self.entry(artifacts=["hw/verification.csv"])
        entry["artifacts"].append({"path": "hw/.cad/builds.json", "sha256": "0" * 64})
        problems = self.refusal(entry)
        self.assertEqual([p.code for p in problems], ["evidence.scratch_path"])
        self.assertIn("hw/.cad/builds.json", problems[0].message)

    def test_every_prose_field_is_scanned(self) -> None:
        for field in ("tool", "producer", "note", "coverage", "uncertainty"):
            with self.subTest(field):
                problems = self.refusal(self.entry(**{field: "run .cad-work/layout/regional_v1.py"}))
                self.assertEqual([p.code for p in problems], ["evidence.scratch_path"])
                self.assertEqual(problems[0].where, field)

    def test_a_disposition_reason_is_scanned(self) -> None:
        disposition = {"finding": "f1", "status": "declined", "decision": "DR-001",
                       "producer": {"bundle": "hw/checks/v1/lever", "manifest_sha256": V1},
                       "reason": "declined, see /tmp/review.md"}
        problems = paths_module.disposition_problems(self.paths, disposition)
        self.assertEqual([p.code for p in problems], ["evidence.scratch_path"])


class NotAPath(Project):
    def test_prose_that_is_not_a_path_passes(self) -> None:
        for prose in ("Fit is ~5 mm", "thread 1/4-20 UNC", "screw M6x20", "tolerance ±0.1/0.2", "load 5 N/mm2.",
                      "speed 10 mm/s", "pass/fail", "ratio 3/8 in", "see https://example.com/datasheet.pdf",
                      "rev 1.2/1.3", "ISO 286-1 and/or ANSI B4.1", "sealed v3 (full-sweep)"):
            with self.subTest(prose):
                self.assertEqual(self.refusal(self.entry(method=prose)), [], prose)

    def test_a_tracked_in_tree_path_passes(self) -> None:
        self.assertEqual(self.refusal(self.entry(method="rationale in notes/method.md:3.")), [])

    def test_a_sealed_version_path_the_entry_lists_passes(self) -> None:
        entry = self.entry(method="read back hw/versions/v3/assembly.step", artifacts=["hw/versions/v3/assembly.step"])
        self.assertEqual(self.refusal(entry), [])

    def test_a_sealed_version_path_the_entry_does_not_list_is_uncited(self) -> None:
        problems = self.refusal(self.entry(method="read back hw/versions/v3/assembly.step"))
        self.assertEqual([p.code for p in problems], ["evidence.uncited_artifact"])


class AcceptancePoints(Project):
    def test_the_acceptance_inventory_refuses_scratch_text(self) -> None:
        acceptance.write_ledger(self.hw, [self.entry(method=VETOED_METHOD)])
        with self.assertRaisesRegex(evidence.EvidenceError, r"\.cad-work/layout/regional_v1\.py"):
            evidence.acceptance_inventory(self.hw, impact.read_record(self.hw))

    def test_the_acceptance_inventory_accepts_the_promoted_entry(self) -> None:
        method = f"script {SCRIPT}, result {RESULT}"
        acceptance.write_ledger(self.hw, [self.entry(method=method, artifacts=[SCRIPT, RESULT])])
        inventory = evidence.acceptance_inventory(self.hw, impact.read_record(self.hw))
        self.assertIn(RESULT, inventory)

    def test_the_ledger_findings_report_scratch_text_as_errors(self) -> None:
        acceptance.write_ledger(self.hw, [self.entry(method=VETOED_METHOD)])
        findings = evidence.ledger_findings(impact.read_record(self.hw), approved=lambda _: True, today=TODAY,
                                            paths=self.paths)
        self.assertEqual({f.code for f in findings}, {"evidence.scratch_path"})
        self.assertEqual({f.severity for f in findings}, {"error"})


class OutsideGit(unittest.TestCase):
    def test_a_project_that_is_no_git_work_tree_still_refuses_scratch_and_outside(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-evidence-paths-") as name:
            root = Path(name).resolve()
            (root / "hw").mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            paths = evidence.project_paths(root / "hw")
            self.assertIsNone(paths.tracked)
            self.assertIsNone(paths.judge("notes/a.md").problem)
            self.assertIsNotNone(paths.judge(".cad-work/x.json").problem)
            self.assertIsNotNone(paths.judge("../x.json").problem)


if __name__ == "__main__":
    unittest.main()
