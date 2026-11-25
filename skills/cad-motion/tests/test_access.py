#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Assembly order and access (plan section 11 row 15).

Pure order and closure checks run always (cad-record assembly_operations). The geometric part and tool
paths run access.py through `uv run --script` on access_fixture.py's built geometry, so they skip
without uv. Run: python3 -m unittest discover -s skills/cad-motion/tests -p test_access.py
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
SCRIPT = HERE.parent / "scripts" / "access.py"
FIXTURES = HERE / "fixtures"


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402


ops = load("cad_record_assembly_operations", SKILLS / "cad-record" / "scripts" / "assembly_operations.py")
assembly_data = load("cad_record_assembly_data", SKILLS / "cad-record" / "scripts" / "assembly_data.py")
fx = load("cad_record_mi_fixtures", SKILLS / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")
access = load("cad_motion_access", SCRIPT)
PNS = ("TST-0101", "TST-0102", "TST-0103", "TST-0104", "TST-0105")
TITLES = {"TST-0101": "Housing", "TST-0102": "Bearing", "TST-0103": "Cover", "TST-0104": "Cover screw",
          "TST-0105": "Bracket"}


def operations(name: str, socket_sha256: str = "0" * 64) -> list[dict]:
    text = (FIXTURES / name).read_text(encoding="utf-8").replace("{socket_sha256}", socket_sha256)
    return yaml.safe_load(text)["operations"]


def reordered(items: list[dict], *ids: str) -> list[dict]:
    by_id = {item["id"]: item for item in items}
    return [copy.deepcopy(by_id[ident]) for ident in ids]


def inventory():
    return assembly_data.normalize({"subassemblies": [{"id": "main", "title": "Main", "parts": list(PNS)}]})


def validator(definition: str):
    target = definition if definition.startswith("https:") else \
        f"https://srobroek.com/cad/schemas/manufacturing.schema.json#/$defs/{definition}"
    return fx.ref_validator(target)


class OrderTests(unittest.TestCase):
    """The pure order and closure check: closure before install fails; a valid order passes, its scope stated."""

    def check(self, items: list[dict]):
        return ops.check_order(ops.normalize({"operations": items}), inventory())

    def assert_schema_valid(self, report) -> None:
        finding, outcome = validator("finding"), validator("obligation_outcome")
        for item in report.findings:
            self.assertEqual(list(finding.iter_errors(item)), [], item["id"])
        for row in report.outcomes:
            self.assertEqual(list(outcome.iter_errors(row)), [], row["obligation"])

    def test_a_bearing_cannot_be_installed_after_the_cover_closes_it(self) -> None:
        # The bracket and screw complete the assembly, so the closure is the only defect.
        rest = reordered(operations("blocked_socket.yaml"), "mount-bracket", "fasten-cover")
        report = self.check(operations("closed_bearing_install.yaml") + rest)
        self.assert_schema_valid(report)
        self.assertEqual(report.outcome("assembly.order@op:fit-cover")["outcome"], "pass")
        failed = report.outcome("assembly.order@op:seat-bearing")
        self.assertEqual(failed["outcome"], "fail")
        [finding] = report.findings
        self.assertEqual(finding["code"], "assembly.closed_before_install")
        self.assertEqual(finding["severity"], "critical")
        self.assertIn("fit-cover encloses", finding["measured"]["text"])
        self.assertEqual(failed["findings"], [finding["id"]])

    def test_a_valid_order_passes_with_its_limited_scope(self) -> None:
        report = self.check(operations("blocked_socket.yaml"))
        self.assert_schema_valid(report)
        self.assertEqual({row["obligation"]: row["outcome"] for row in report.outcomes},
                         {**{f"assembly.order@op:{ident}": "pass"
                             for ident in ("seat-bearing", "fit-cover", "mount-bracket", "fasten-cover")},
                          "assembly.order@product": "pass"})
        self.assertEqual(report.findings, ())
        self.assertIn("part paths and tool reach are assembly.access", report.outcomes[0]["coverage"])

    def test_a_receiving_part_installed_later_and_a_late_predecessor_fail(self) -> None:
        items = operations("blocked_socket.yaml")
        late_into = reordered(items, "seat-bearing", "fasten-cover", "fit-cover", "mount-bracket")
        report = self.check(late_into)
        self.assertEqual(report.outcome("assembly.order@op:fasten-cover")["outcome"], "fail")
        self.assertIn("assembly.into_absent", {f["code"] for f in report.findings})
        constrained = copy.deepcopy(items)
        constrained[0]["predecessors"] = ["fit-cover"]
        report = self.check(constrained)
        self.assertEqual(report.outcome("assembly.order@op:seat-bearing")["outcome"], "fail")
        self.assertIn("assembly.order_impossible", {f["code"] for f in report.findings})

    def test_an_enclosed_part_cannot_be_disassembled_or_adjusted(self) -> None:
        pull = {"id": "pull-bearing", "kind": "disassemble", "part": "TST-0102", "into": "TST-0101", "direction": [0, 0, 1]}
        report = self.check(operations("blocked_socket.yaml") + [pull])  # the full sequence, then the pull
        self.assertEqual(report.outcome("assembly.order@op:pull-bearing")["outcome"], "fail")
        self.assertEqual({f["code"] for f in report.findings}, {"assembly.closed_before_install"})

    def test_unknown_operations_stay_unknown(self) -> None:
        report = ops.check_order(ops.normalize({"operations": fx.unknown("not planned yet")}))
        self.assertEqual([(row["obligation"], row["outcome"]) for row in report.outcomes],
                         [("intent.assembly_operations@product", "unknown")])

    def test_the_access_plan_lists_obstacles_and_keeps_an_unrecognised_tool_a_note(self) -> None:
        items = operations("blocked_socket.yaml")
        items[3]["tools"] = ["torque driver"]
        plan = {request.operation.id: request for request in ops.access_plan(ops.normalize({"operations": items}),
                                                                           inventory())}
        self.assertEqual(plan["seat-bearing"].path_obstacles, frozenset())  # only the receiving housing is in place
        self.assertEqual(plan["fit-cover"].path_obstacles, frozenset({"TST-0102"}))
        self.assertEqual(plan["fasten-cover"].tool_obstacles, frozenset({"TST-0101", "TST-0102", "TST-0103", "TST-0105"}))
        self.assertEqual(plan["fasten-cover"].frame, "TST-0103")
        self.assertEqual(plan["fasten-cover"].unknown, ())
        self.assertTrue(any("torque driver" in note for note in plan["fasten-cover"].notes))

    def test_a_tool_free_hold_gets_an_explicit_not_applicable_row_with_its_reason(self) -> None:
        items = operations("blocked_socket.yaml")
        items.append({"id": "hold-bracket", "kind": "inspect", "part": "TST-0105", "tools": ["hold"],
                      "acceptance": {"kind": "inspected", "features": ["bracket_foot"]}})
        request = next(r for r in ops.access_plan(ops.normalize({"operations": items}), inventory())
                       if r.operation.id == "hold-bracket")
        entry = access.OperationAccess(request.operation, unknown=list(request.unknown), notes=list(request.notes),
                                       applicable=request.applicable)
        row = access.access_outcome(entry, [])
        self.assertEqual((row["obligation"], row["outcome"]), ("assembly.access@op:hold-bracket", "not_applicable"))
        self.assertIn("moves no part and no tool or hand reaches it", row["uncertainty"])
        self.assertEqual(list(validator("obligation_outcome").iter_errors(row)), [])

    def test_an_unknown_access_row_keeps_its_instruction_notes(self) -> None:
        items = operations("blocked_socket.yaml")
        items.append({"id": "adjust-bracket", "kind": "adjust", "part": "TST-0105", "tools": ["frobnicator", "3 mm hex key"],
                      "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]}})
        request = next(r for r in ops.access_plan(ops.normalize({"operations": items}), inventory())
                       if r.operation.id == "adjust-bracket")
        entry = access.OperationAccess(request.operation, unknown=list(request.unknown), notes=list(request.notes),
                                       applicable=request.applicable)
        findings = access.access_findings(entry)
        row = access.access_outcome(entry, findings)
        self.assertEqual(row["outcome"], "unknown", "a note never changes the verdict")
        self.assertIn("no approach direction", row["uncertainty"])
        self.assertIn("frobnicator", row["uncertainty"])
        self.assertEqual(list(validator("obligation_outcome").iter_errors(row)), [])


def access_record(items: list[dict]) -> dict:
    """A record version 2 project of the five fixture parts with `items` as its operations."""
    parts = [fx.part(pn, TITLES[pn], process="cnc") for pn in PNS]
    interface = fx.interface("IF-001", "TST-0101", "TST-0102", "mounting",
                             limits={"press_fit": {"fit": "22 H7/p6", "pair": ["TST-0102", "TST-0101"]}})
    return fx.record(parts=parts, interfaces=[interface], operations=items)


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the access geometry and run access.py")
class GeometricAccessTests(unittest.TestCase):
    """access.py on built geometry: a blocked socket and a closed bearing fail, the valid order passes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name).resolve()
        build = subprocess.run(["uv", "run", "--quiet", "--script", str(FIXTURES / "access_fixture.py"),
                                str(cls.root / "geometry")], capture_output=True, text=True, timeout=900, check=False)
        if build.returncode != 0:
            raise RuntimeError(build.stdout + build.stderr)
        cls.socket_sha = hashlib.sha256((cls.root / "geometry" / "socket.step").read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.folder.cleanup()

    def project(self, name: str, items: list[dict], extra_interfaces: tuple[dict, ...] = ()) -> Path:
        documents = access_record(items)
        documents["interfaces.yaml"]["interfaces"].extend(extra_interfaces)
        hw = fx.write_record(self.root / name / "hw", documents)
        source = hw / "manufacturing" / "sources" / "socket.step"
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / "geometry" / "socket.step", source)
        return hw

    def run_script(self, script: Path, hw: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        attach = [arg for pn in PNS[1:] for arg in ("--attach", f"{pn}=TST-0101")]
        return subprocess.run(["uv", "run", "--quiet", "--script", str(script), "--hw", str(hw), *extra, *attach],
                              capture_output=True, text=True, timeout=1800, check=False)

    def run_access(self, hw: Path, *extra: str) -> tuple[int, dict, str]:
        proc = self.run_script(SCRIPT, hw, *extra)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def versioned(self, name: str) -> Path:
        """A project of the valid order with its v1 version folder: parts, record snapshot and manifest."""
        items = reordered(operations("blocked_socket.yaml", self.socket_sha),
                          "seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")
        hw = self.project(name, items)
        version = hw / "versions" / "v1"
        (version / "parts").mkdir(parents=True)
        for step in (self.root / "geometry" / "parts").iterdir():
            shutil.copyfile(step, version / "parts" / step.name)
        for path in hw.rglob("*"):
            rel = path.relative_to(hw)
            if path.is_file() and rel.parts[0] not in ("versions", "decisions") and (
                    rel.suffix == ".yaml" or rel.parts[:2] == ("manufacturing", "sources")):
                target = version / "record" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        files = {p.relative_to(version).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(version.rglob("*")) if p.is_file()}
        (version / "manifest.json").write_text(json.dumps({"label": "v1", "files": files}), encoding="utf-8")
        return hw

    def parts_args(self) -> tuple[str, ...]:
        return ("--parts-dir", str(self.root / "geometry" / "parts"))

    @staticmethod
    def outcomes(result: dict) -> dict[str, str]:
        return {row["obligation"]: row["outcome"] for row in result["outcomes"]}

    def assert_schema_valid(self, result: dict) -> None:
        finding, outcome = validator("finding"), validator("obligation_outcome")
        for item in result["findings"]:
            self.assertEqual(list(finding.iter_errors(item)), [], item["id"])
        for row in result["outcomes"]:
            self.assertEqual(list(outcome.iter_errors(row)), [], row["obligation"])

    def test_a_socket_the_bracket_blocks_fails_its_access(self) -> None:
        hw = self.project("blocked", operations("blocked_socket.yaml", self.socket_sha))
        code, result, _ = self.run_access(hw, *self.parts_args())
        self.assert_schema_valid(result)
        self.assertEqual(code, 1)
        outcomes = self.outcomes(result)
        self.assertEqual(outcomes["assembly.access@op:fasten-cover"], "fail")
        for ident in ("seat-bearing", "fit-cover", "mount-bracket"):
            self.assertEqual(outcomes[f"assembly.access@op:{ident}"], "pass", ident)
        for ident in ("seat-bearing", "fit-cover", "mount-bracket", "fasten-cover"):
            self.assertEqual(outcomes[f"assembly.order@op:{ident}"], "pass", ident)
        [blocked] = [f for f in result["findings"] if f["obligation"] == "assembly.access@op:fasten-cover"]
        self.assertEqual(blocked["code"], "assembly.tool_blocked")
        self.assertIn("TST-0105", blocked["consequence"])
        self.assertEqual(blocked["criterion"]["source"]["origin"], "benchhand")
        screw, socket = result["detail"]["paths"]["fasten-cover"]
        self.assertEqual((screw["status"], socket["status"]), ("clear", "collision"))
        self.assertEqual((screw["label"], screw["route"], socket["route"]), ("fasten-cover/part", "straight_default", "approach"),
                         "without a declared path the straight line along the direction is the reported default")
        self.assertTrue(result["complete"])
        self.assertEqual(result["result"], "fail")

    def test_the_valid_order_passes_with_its_coverage_stated_and_publishes_a_packet(self) -> None:
        hw = self.versioned("valid")
        code, result, stderr = self.run_access(hw, "--version", "v1")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(set(self.outcomes(result).values()), {"pass"})
        self.assertEqual(result["findings"], [])
        coverage = next(row["coverage"] for row in result["outcomes"]
                        if row["obligation"] == "assembly.access@op:fasten-cover")
        self.assertIn("curved paths, tool swing and posture, the hand's grip, forces and other poses are not covered", coverage)
        bundle = hw / "checks" / "v1" / "assembly-access"
        packet = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(list(validator("https://srobroek.com/cad/schemas/manufacturing-check-result.schema.json")
                              .iter_errors(packet)), [])
        self.assertEqual((packet["result"], packet["complete"]), ("pass", True))
        self.assertTrue((bundle / "evidence" / "access.json").is_file())
        again, rerun, _ = self.run_access(hw, "--version", "v1")
        self.assertEqual((again, rerun["reused"]), (0, True))

    def test_a_published_pass_is_not_reused_after_the_access_policy_changes(self) -> None:
        """The rule digest does not cover access.py or engagement.py, so the recipe must. The scripts run from a private
        copy of the tree so that one comment line appended to engagement.py stands for any policy change."""
        tree = self.root / "policy-tree"
        (tree / "skills" / "cad-motion").mkdir(parents=True)
        (tree / "scripts").symlink_to(SKILLS.parent / "scripts")
        for skill in SKILLS.iterdir():
            if skill.name != "cad-motion":
                (tree / "skills" / skill.name).symlink_to(skill)
        motion = SKILLS / "cad-motion"
        for entry in motion.iterdir():
            target = tree / "skills" / "cad-motion" / entry.name
            if entry.name == "scripts":
                shutil.copytree(entry, target, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                target.symlink_to(entry)
        script = tree / "skills" / "cad-motion" / "scripts" / "access.py"
        hw = self.versioned("policy")
        first = self.run_script(script, hw, "--version", "v1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(json.loads(first.stdout)["reused"])
        same = self.run_script(script, hw, "--version", "v1")
        self.assertEqual((same.returncode, json.loads(same.stdout)["reused"]), (0, True), same.stderr)
        with (script.parent / "engagement.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# a later change of the engagement policy\n")
        changed = self.run_script(script, hw, "--version", "v1")
        self.assertEqual(changed.returncode, 2, changed.stdout + changed.stderr)  # another recipe, not the old PASS
        replaced = self.run_script(script, hw, "--version", "v1", "--replace")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertFalse(json.loads(replaced.stdout)["reused"])

    def test_a_closed_cover_blocks_the_bearing_path_and_fails_the_order(self) -> None:
        hw = self.project("closed", operations("closed_bearing_install.yaml"))
        code, result, _ = self.run_access(hw, *self.parts_args())
        self.assertEqual(code, 1)
        outcomes = self.outcomes(result)
        self.assertEqual(outcomes["assembly.order@op:seat-bearing"], "fail")
        self.assertEqual(outcomes["assembly.access@op:seat-bearing"], "fail")
        codes = {f["code"] for f in result["findings"] if f["subject"] == {"assembly_operation": "seat-bearing"}}
        self.assertEqual(codes, {"assembly.closed_before_install", "assembly.part_path_blocked"})
        path = next(f for f in result["findings"] if f["code"] == "assembly.part_path_blocked")
        self.assertIn("TST-0103", path["consequence"])

    def test_a_malformed_running_or_locating_fit_is_an_input_error_though_it_never_exempts(self) -> None:
        """A misspelled key gets the reader's input error, as it did before this fit kind lost its exemption."""
        typos = {"running_fit": {"fit": "5 f7", "pairs": ["TST-0103", "TST-0104"]},
                 "locating_fit": {"fitt": "5 H7/f7", "pair": ["TST-0103", "TST-0104"]}}
        for kind, declaration in typos.items():
            with self.subTest(kind=kind):
                items = operations("blocked_socket.yaml", self.socket_sha)
                bad = fx.interface("IF-009", "TST-0103", "TST-0104", "mounting", limits={kind: declaration})
                hw = self.project(f"typo-{kind}", items, (bad,))
                attach = [arg for pn in PNS[1:] for arg in ("--attach", f"{pn}=TST-0101")]
                proc = subprocess.run(["uv", "run", "--quiet", "--script", str(SCRIPT), "--hw", str(hw),
                                       *self.parts_args(), *attach], capture_output=True, text=True, timeout=1800,
                                      check=False)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn(f"interfaces.yaml#/interfaces/1/limits/{kind}", proc.stderr)

    def valid_order(self) -> list[dict]:
        return reordered(operations("blocked_socket.yaml", self.socket_sha),
                         "seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")

    def test_a_named_common_tool_gets_its_standard_envelope_and_a_real_reach_result(self) -> None:
        """Driven after the bracket is mounted: the bracket hole (r 3.5) passes a 3 mm hex key (ISO 2936 e max 3.39)
        and a PH2 blade (ISO 8764-1, 6 mm), not an 8 mm hex key (e max 9.09)."""
        cases = {"3 mm hex key": ("pass", "3 mm hex key"), "PH2 screwdriver": ("pass", "PH2 screwdriver"),
                 "8 mm hex key": ("fail", "8 mm hex key")}
        for name, (expected, designation) in cases.items():
            with self.subTest(tool=name):
                items = operations("blocked_socket.yaml", self.socket_sha)
                del items[3]["tool_access"]
                items[3]["tools"] = [name]
                hw = self.project(f"named-{designation.replace(' ', '-')}", items)
                code, result, _ = self.run_access(hw, *self.parts_args())
                self.assert_schema_valid(result)
                self.assertEqual(self.outcomes(result)["assembly.access@op:fasten-cover"], expected)
                self.assertEqual(code, 0 if expected == "pass" else 1)
                tool = next(p for p in result["detail"]["paths"]["fasten-cover"] if p["route"] == "standard_envelope")
                self.assertEqual(tool["envelope"]["designation"], designation)
                self.assertEqual(tool["status"], "clear" if expected == "pass" else "collision")
                if expected == "fail":
                    blocked = next(f for f in result["findings"] if f["code"] == "assembly.tool_blocked")
                    self.assertIn("TST-0105", blocked["consequence"])

    def test_a_cited_step_solid_overrides_the_standard_envelope(self) -> None:
        """The cited socket solid is checked, not the 3 mm hex key the name would give: the bracket blocks it."""
        items = operations("blocked_socket.yaml", self.socket_sha)
        items[3]["tool_access"][0]["tool"] = "3 mm hex key"
        hw = self.project("override", items)
        code, result, _ = self.run_access(hw, *self.parts_args())
        self.assertEqual((code, self.outcomes(result)["assembly.access@op:fasten-cover"]), (1, "fail"))
        routes = [p["route"] for p in result["detail"]["paths"]["fasten-cover"]]
        self.assertEqual(routes, ["straight_default", "approach"])

    def test_a_tool_is_placed_at_its_declared_contact_not_the_part_centre(self) -> None:
        """A 3 mm hex key adjusts the cover (60 x 60, centred on the axis) from above, after the bracket is mounted:
        at the cover's centre it clears everything; at x 30, under the bracket's solid plate (x 14 to 40, y -8 to 8,
        z 40 to 44), the bracket blocks it, though the cover's box centre is clear."""
        items = self.valid_order()
        for ident, x in (("adjust-off-centre", 30), ("adjust-centre", 0)):
            items.append({"id": ident, "kind": "adjust", "part": "TST-0103",
                          "acceptance": {"kind": "adjusted", "features": ["cover_top"]},
                          "tool_access": [{"tool": "3 mm hex key", "approach": [0, 0, -1], "contact": [x, 0, 24]}]})
        hw = self.project("contact", items)
        code, result, stderr = self.run_access(hw, *self.parts_args())
        self.assert_schema_valid(result)
        outcomes = self.outcomes(result)
        self.assertEqual((outcomes["assembly.access@op:adjust-off-centre"], outcomes["assembly.access@op:adjust-centre"]),
                         ("fail", "pass"), stderr)
        self.assertEqual(code, 1)
        blocked = [f for f in result["findings"] if f["code"] == "assembly.tool_blocked"]
        self.assertEqual([f["subject"] for f in blocked], [{"assembly_operation": "adjust-off-centre"}])
        self.assertIn("TST-0105", blocked[0]["consequence"])

    def test_a_hand_step_is_checked_with_the_hand_and_a_bench_step_is_not_applicable(self) -> None:
        items = self.valid_order()
        items[3]["tools"] = ["by hand"]
        items.append({"id": "inspect-bracket", "kind": "inspect", "part": "TST-0105", "tools": ["by eye", "on the bench"],
                      "acceptance": {"kind": "inspected", "features": ["bracket_foot"]}})
        hw = self.project("hand", items)
        code, result, stderr = self.run_access(hw, *self.parts_args())
        self.assert_schema_valid(result)
        outcomes = self.outcomes(result)
        self.assertEqual(outcomes["assembly.access@op:mount-bracket"], "pass")
        self.assertEqual(outcomes["assembly.access@op:inspect-bracket"], "not_applicable")
        self.assertEqual((code, result["result"]), (0, "pass"), stderr)
        hand = next(p for p in result["detail"]["paths"]["mount-bracket"] if p["route"] == "standard_envelope")
        self.assertEqual((hand["envelope"]["kind"], hand["status"]), ("hand", "clear"))

    def test_an_unrecognised_tool_name_is_a_note_not_an_unknown_obligation(self) -> None:
        items = self.valid_order()
        del items[2]["tool_access"]
        items[2]["tools"] = ["frobnicator"]
        hw = self.project("note", items)
        code, result, _ = self.run_access(hw, *self.parts_args())
        row = next(r for r in result["outcomes"] if r["obligation"] == "assembly.access@op:fasten-cover")
        self.assertEqual((code, row["outcome"]), (0, "pass"))
        self.assertIn("frobnicator", row["uncertainty"])
        self.assertNotIn("assembly.access_unknown", {f["code"] for f in result["findings"]})
        self.assertTrue(any("frobnicator" in note for note in result["detail"]["notes"]["fasten-cover"]))

    def test_a_tool_free_hold_is_not_applicable_and_a_declared_tool_on_a_hold_is_checked(self) -> None:
        items = operations("blocked_socket.yaml", self.socket_sha)
        valid = reordered(items, "seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")
        cure = {"id": "cure-bracket", "kind": "hold", "part": "TST-0105", "fixture": "bench clamp",
                "orientation": [0, 0, 1], "duration": fx.known(fx.quantity(24, "h"))}
        code, result, stderr = self.run_access(self.project("hold", [*valid, cure]), *self.parts_args())
        self.assertEqual(code, 0, stderr)
        [row] = [r for r in result["outcomes"] if r["obligation"] == "assembly.access@op:cure-bracket"]
        self.assertEqual(row["outcome"], "not_applicable")
        self.assertIn("names no tool, so it reaches nothing", row["coverage"])
        self.assertEqual(self.outcomes(result)["assembly.order@op:cure-bracket"], "pass")
        self.assertEqual((result["result"], result["complete"]), ("pass", True))
        self.assert_schema_valid(result)
        clamped = {**cure, "tools": ["spring clamp"]}
        code, result, stderr = self.run_access(self.project("clamped", [*valid, clamped]), *self.parts_args())
        self.assertEqual(code, 0, stderr)
        [row] = [r for r in result["outcomes"] if r["obligation"] == "assembly.access@op:cure-bracket"]
        self.assertEqual((row["outcome"], row["reason"]), ("not_assessed", "no_capability_profile"))
        self.assertIn("'spring clamp' has no cited envelope solid", row["uncertainty"])
        self.assertEqual(result["result"], "not_assessed", "an advisory row is never a pass and blocks nothing")
        self.assert_schema_valid(result)
        socket = copy.deepcopy(next(item for item in items if item["id"] == "fasten-cover")["tool_access"])
        held = {"id": "hold-screw", "kind": "hold", "part": "TST-0104", "fixture": "hex socket", "orientation": [0, 0, 1],
                "duration": fx.known(fx.quantity(10, "min")), "tool_access": socket}
        code, result, _ = self.run_access(self.project("held", [*valid, held]), *self.parts_args())
        self.assertEqual(code, 1)
        self.assertEqual(self.outcomes(result)["assembly.access@op:hold-screw"], "fail", "the bracket blocks the socket")
        self.assertIn("assembly.tool_blocked", {f["code"] for f in result["findings"]
                                                if f["obligation"] == "assembly.access@op:hold-screw"})

    def test_a_blocked_cited_tool_after_an_uncited_one_is_the_tool_blamed(self) -> None:
        items = operations("blocked_socket.yaml", self.socket_sha)
        valid = reordered(items, "seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")
        socket = copy.deepcopy(next(item for item in items if item["id"] == "fasten-cover")["tool_access"])
        held = {"id": "hold-screw", "kind": "hold", "part": "TST-0104", "fixture": "hex socket", "orientation": [0, 0, 1],
                "duration": fx.known(fx.quantity(10, "min")), "tools": ["spring clamp"], "tool_access": socket}
        code, result, _ = self.run_access(self.project("mixed", [*valid, held]), *self.parts_args())
        self.assertEqual(code, 1)
        [blocked] = [f for f in result["findings"] if f["obligation"] == "assembly.access@op:hold-screw"
                     and f["code"] == "assembly.tool_blocked"]
        self.assertIn("tool '3 mm hex socket on a ratchet' collides", blocked["consequence"])
        self.assertNotIn("spring clamp", blocked["consequence"])

    def test_a_declared_insertion_path_is_travelled_leg_by_leg(self) -> None:
        """The screw slid in sideways above the cover, then down: the bracket blocks the first leg, though the
        default straight path down through the bracket hole is clear."""
        items = operations("blocked_socket.yaml", self.socket_sha)
        items[3]["path"] = {"start": [-25, 0, 22], "via": [[0, 0, 22]]}
        hw = self.project("declared", items)
        code, result, _ = self.run_access(hw, *self.parts_args())
        self.assertEqual(code, 1)
        self.assertEqual(self.outcomes(result)["assembly.access@op:fasten-cover"], "fail")
        legs = [p for p in result["detail"]["paths"]["fasten-cover"] if p["route"] == "declared_path"]
        self.assertEqual([(p["label"], p["travel_mm"]) for p in legs], [("fasten-cover/part/leg1", 25.0), ("fasten-cover/part/leg2", 22.0)])
        self.assertEqual(legs[0]["status"], "collision")
        self.assertEqual({c["obstacle"] for c in legs[0]["collisions"]}, {"TST-0105"})
        blocked = next(f for f in result["findings"] if f["code"] == "assembly.part_path_blocked")
        self.assertIn("the declared insertion path", blocked["uncertainty"])


HOUSING, SLEEVE, PIN = "TST-0101", "TST-0102", "TST-0104"


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the engagement geometry and run access.py")
class EngagementAccessTests(unittest.TestCase):
    """access.py on a pin pressed into a sleeve: which declaration may exempt the interference, and for how long.

    The press pin interferes with the sleeve for 8 mm of travel back from its installed pose, the bead pin for 7 mm."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name).resolve()
        build = subprocess.run(["uv", "run", "--quiet", "--script", str(FIXTURES / "engagement_fixture.py"),
                                str(cls.root / "geometry")], capture_output=True, text=True, timeout=900, check=False)
        if build.returncode != 0:
            raise RuntimeError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.folder.cleanup()

    def outcome(self, name: str, pin: str, limits: dict, path: dict | None = None) -> str:
        """The access outcome of inserting the pin when its pair with the sleeve declares `limits`."""
        seat = fx.interface("IF-001", HOUSING, SLEEVE, "mounting",
                            limits={"press_fit": {"fit": "22 H7/p6", "pair": [SLEEVE, HOUSING]}})
        fit = fx.interface("IF-002", SLEEVE, PIN, "mounting", limits=limits)
        insert = {"id": "insert-pin", "kind": "join", "part": PIN, "into": HOUSING, "direction": [0, 0, -1],
                  "acceptance": {"kind": "standoff", "features": ["pin_foot", "bore_floor"]}}
        if path is not None:
            insert["path"] = path
        seated = {"id": "seat-sleeve", "kind": "press_fit", "part": SLEEVE, "into": HOUSING, "interface": "IF-001",
                  "direction": [0, 0, -1], "acceptance": {"kind": "seated", "features": ["outer_face", "seat_floor"]}}
        parts = [fx.part(pn, pn, process="cnc") for pn in (HOUSING, SLEEVE, PIN)]
        hw = fx.write_record(self.root / name / "hw", fx.record(parts=parts, interfaces=[seat, fit],
                                                                 operations=[seated, insert]))
        attach = [arg for pn in (SLEEVE, PIN) for arg in ("--attach", f"{pn}={HOUSING}")]
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(SCRIPT), "--hw", str(hw), "--parts-dir",
                               str(self.root / "geometry" / pin / "parts"), *attach],
                              capture_output=True, text=True, timeout=1800, check=False)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        rows = {row["obligation"]: row["outcome"] for row in json.loads(proc.stdout)["outcomes"]}
        return rows["assembly.access@op:insert-pin"]

    @staticmethod
    def press(length: float | None, pair: tuple[str, str] = (PIN, SLEEVE)) -> dict:
        return {"fit": "10 H7/p6", "pair": list(pair), **({} if length is None else {"engagement_length_mm": length})}

    @staticmethod
    def locating(length: float | None) -> dict:
        return {"fit": "10 H7/f7", "pair": [PIN, SLEEVE], **({} if length is None else {"engagement_length_mm": length})}

    def test_an_engagement_that_ends_inside_an_earlier_leg_stays_exempt_while_that_leg_is_refined(self) -> None:
        """The bead pin goes in down a path split 2 mm above the installed pose. Its 7 mm engagement runs back across
        the waypoint and ends 5 mm into the first leg, where the clear shank interval needs refining."""
        split = {"start": [0, 0, 9.7], "via": [[0, 0, 2]]}
        self.assertEqual(self.outcome("split-within", "bead", {"press_fit": self.press(7.0)}, split), "pass")
        self.assertEqual(self.outcome("split-short", "bead", {"press_fit": self.press(6.5)}, split), "fail")

    def test_the_engagement_length_is_the_travel_over_which_interference_lasts(self) -> None:
        """The bore is 8 mm deep. The long pin's press band runs 0.3 mm above the bore mouth, which adds no
        interference, so the bore depth is enough and 0.3 mm less fails. The 6 mm press pin overlaps the bore for
        6 mm at its installed pose but interferes for 8 mm of travel, so declaring the overlap fails."""
        cases = (("long", 8.0, "pass"), ("long", 7.7, "fail"), ("press", 8.0, "pass"), ("press", 6.0, "fail"))
        for pin, length, expected in cases:
            with self.subTest(pin=pin, engagement_length_mm=length):
                self.assertEqual(self.outcome(f"travel-{pin}-{length}", pin, {"press_fit": self.press(length)}), expected)

    def test_a_heat_set_exempts_interference_only_over_its_own_engagement_length(self) -> None:
        def heat_set(length: float | None) -> dict:
            entry = {"pair": [SLEEVE, PIN], "hole_d": 10, "insert": "TST insert"}
            return {"heat_set": [entry if length is None else {**entry, "engagement_length_mm": length}]}

        for length, expected in ((8.0, "pass"), (7.5, "fail"), (None, "unknown")):
            with self.subTest(engagement_length_mm=length):
                self.assertEqual(self.outcome(f"heat-set-{length}", "press", heat_set(length)), expected)

    def test_a_locating_fit_on_the_same_pair_neither_enlarges_nor_erases_the_press_fit_cap(self) -> None:
        cases = (("longer", self.press(7.5), self.locating(20.0), "fail"),
                 ("missing", self.press(8.0), self.locating(None), "pass"),
                 ("press-missing", self.press(None), self.locating(20.0), "unknown"))
        for name, press, locating, expected in cases:
            with self.subTest(locating=name):
                self.assertEqual(self.outcome(f"locating-{name}", "press",
                                              {"press_fit": press, "locating_fit": locating}), expected)


class ExitCodeTests(unittest.TestCase):
    """D37: a packet that assessed nothing exits 0; an unknown or failing one exits 1. No emitter yet shows not_assessed, so
    the seam is the status check_packet.emit hands back."""

    def test_the_exit_code_follows_the_packet_status(self) -> None:
        for status, code in {"pass": 0, "not_assessed": 0, "unknown": 1, "fail": 1}.items():
            with self.subTest(status=status), tempfile.TemporaryDirectory() as hw, \
                    patch.object(access, "assess", return_value=({"dependencies": {}}, {})), \
                    patch.object(access.check_packet, "emit", return_value=({"result": status}, status)), \
                    patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(access.main(["--hw", hw, "--parts-dir", hw]), code)


if __name__ == "__main__":
    unittest.main()
