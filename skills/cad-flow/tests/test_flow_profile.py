#!/usr/bin/env python3
"""cad-flow flow_profile.py: the project-start questions, the record of their answers in product.yaml
flow.profile with its decision, the check that refuses a missing or unasked profile once it is due, and
the flow.conflict rules between the profile and the rest of the record."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "flow_profile.py"
SKILLS = HERE.parents[1]
sys.path.insert(0, str(SKILLS.parent / "scripts"))
from modload import load_module  # noqa: E402

flow_profile = load_module("cad_flow_profile", SCRIPT)
fx = load_module("cad_record_mi_fixtures", SKILLS / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")

# Design section 3 (issue 23, comment 6944452744), copied by hand.
QUESTIONS = [
    "Must any part carry a load where breaking or bending would matter? Should I simulate it?",
    "Which fits must not jam or rattle with real, imperfect parts: only the critical ones, all of them, or none?",
    "How much review before release: full, a quick re-check of retainers, mounts and walls, or none?",
    "Which printer, nozzle and filament? What is the bed size?",
    "Are supports allowed, or must parts print support-free? Do you know your overhang, bridge, thinnest-wall and "
    "smallest-feature limits, or should I wait for a test print?",
    "Will you pick the print orientation, or should I propose options? Which faces must stay clean?",
    "Would you like hinges or joints printed already assembled where that fits?",
    "Should every stage gate wait for your approval, or only intent, variant, freeze and release?",
    "Should generated results (STEP, GLB, renders) be committed to git?",
]

PRODUCT = """\
record_version: 2
id: tst-product
# a comment the profile record keeps
rigor: pro
flow:
  stage: intent
  recheck_from: intent
units: mm
"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


class SchemaContract(unittest.TestCase):
    """The product.schema.json values other packages build on."""

    def test_gated_stage_is_the_agreed_ordered_subset_of_the_stages(self) -> None:
        defs = json.loads((SKILLS / "cad-record" / "schemas" / "product.schema.json").read_text(encoding="utf-8"))["$defs"]
        self.assertEqual(defs["gated_stage"]["enum"], ["intent", "concept", "layout", "parts", "detail", "analysis",
                                                       "design_freeze", "release"])
        self.assertLessEqual(set(defs["gated_stage"]["enum"]), set(defs["stage"]["enum"]))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            flow_profile.write_decision(Path(tmp), "DR-001", status="APPROVED", gate="specify", version="N/A",
                                        title="TST", body="TST\n")

    def test_fdm_is_the_iso_52900_material_extrusion_category(self) -> None:
        schema = json.loads((SKILLS / "cad-record" / "schemas" / "product.schema.json").read_text(encoding="utf-8"))
        processes = schema["$defs"]["profile"]["properties"]["processes"]["properties"]
        self.assertEqual(flow_profile.ISO_52900_CATEGORY["fdm"], "mex")
        self.assertEqual(set(flow_profile.ISO_52900_CATEGORY), set(processes), "every profile process has a category")
        self.assertLessEqual(set(flow_profile.ISO_52900_CATEGORY.values()),
                             set(schema["$defs"]["iso_52900_category"]["enum"]))


class Questions(unittest.TestCase):
    def test_questions_prints_the_nine_project_start_questions_verbatim_and_states_the_motion_sweep(self) -> None:
        result = run("questions")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        numbered = [line for line in lines if line[:2] in {f"{n}." for n in range(1, 10)}]
        self.assertEqual(numbered, [f"{n}. {text}" for n, text in enumerate(QUESTIONS, start=1)])
        self.assertTrue(any("motion sweep" in line and "not asked" in line for line in lines), result.stdout)


class Record(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-flow-profile-")
        self.addCleanup(temporary.cleanup)
        self.hw = Path(temporary.name) / "hw"
        self.hw.mkdir()
        self.product = self.hw / "product.yaml"
        self.product.write_text(PRODUCT, encoding="utf-8")

    def record(self, decision: str = "DR-001", *answers: str) -> subprocess.CompletedProcess[str]:
        return run("record", "--hw", str(self.hw), "--decision", decision, *answers)

    def profile(self) -> Any:
        return yaml.safe_load(self.product.read_text(encoding="utf-8"))["flow"]["profile"]

    def test_record_writes_the_answers_and_marks_every_unasked_question_not_asked(self) -> None:
        result = self.record("DR-001", "--human-approval", "key", "--fea", "off", "--stack-up", "auto", "--critique", "light",
                             "--results-in-git", "no", "--supports", "free", "--overhang-deg", "50", "--warp", "off",
                             "--print-in-place", "offer", "--pip-clearance-xy", "0.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        n = "not_asked"
        self.assertEqual(self.profile(), {
            "decision": "DR-001", "human_approval": "key", "fea": "off", "stack_up": "auto", "critique": "light",
            "motion": "auto", "storage_results_in_git": False,
            "processes": {"fdm": {"orientation": n, "supports": "free", "overhang_deg": 50, "bridge_mm": n, "min_wall_mm": n,
                                  "min_feature_mm": n, "warp": "off", "cosmetic_faces": n, "print_in_place": "offer",
                                  "pip_clearance_mm": {"xy": 0.3, "z": n}}}})
        data = yaml.safe_load(self.product.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in data.items() if k != "flow"},
                         {"record_version": 2, "id": "tst-product", "rigor": "pro", "units": "mm"})
        self.assertEqual((data["flow"]["stage"], data["flow"]["recheck_from"]), ("intent", "intent"))
        self.assertIn("# a comment the profile record keeps\n", self.product.read_text(encoding="utf-8"))

    def test_record_writes_the_answers_as_an_approved_intent_decision(self) -> None:
        self.assertEqual(self.record("DR-004", "--fea", "on").returncode, 0)
        text = (self.hw / "decisions" / "DR-004.md").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[:3], ["Status: APPROVED", "Gate: intent", "Version: N/A"])
        self.assertIn("| fea | on |", text)
        self.assertIn("| processes.fdm.orientation | not_asked |", text)

    def test_record_refuses_a_decision_that_exists_and_changes_nothing(self) -> None:
        (self.hw / "decisions").mkdir()
        (self.hw / "decisions" / "DR-001.md").write_text("# DR-001 an earlier decision\n", encoding="utf-8")
        result = self.record("DR-001", "--fea", "on")
        self.assertEqual(result.returncode, 2)
        self.assertIn("DR-001", result.stderr)
        self.assertEqual(self.product.read_text(encoding="utf-8"), PRODUCT)
        self.assertEqual((self.hw / "decisions" / "DR-001.md").read_text(encoding="utf-8"), "# DR-001 an earlier decision\n")

    def test_a_second_record_replaces_the_profile_under_its_new_decision(self) -> None:
        self.assertEqual(self.record("DR-001", "--fea", "off").returncode, 0)
        self.assertEqual(self.record("DR-002", "--fea", "on").returncode, 0)
        self.assertEqual((self.profile()["decision"], self.profile()["fea"]), ("DR-002", "on"))
        self.assertEqual(self.product.read_text(encoding="utf-8").count("profile:"), 1)

    def test_record_refuses_a_flow_it_cannot_edit_as_text(self) -> None:
        text = PRODUCT.replace("flow:\n  stage: intent\n  recheck_from: intent\n", "flow: {stage: intent, recheck_from: intent}\n")
        self.product.write_text(text, encoding="utf-8")
        result = self.record("DR-001", "--fea", "on")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.product.read_text(encoding="utf-8"), text)
        self.assertFalse((self.hw / "decisions" / "DR-001.md").exists())


class Check(unittest.TestCase):
    """flow_profile.py check, the gate cad_model.py run, validate.py and the stage advance share."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-flow-profile-check-")
        self.addCleanup(temporary.cleanup)
        self.hw = Path(temporary.name) / "hw"
        self.documents = fx.record()
        fx.write_record(self.hw, copy.deepcopy(self.documents))

    def write(self, stage: str = "parts", profile: Any = None, *, process: str = "fdm") -> None:
        documents = copy.deepcopy(self.documents)
        product = documents["product.yaml"]
        product["flow"].update(stage=stage, recheck_from=stage)
        if profile is None:
            del product["flow"]["profile"]
        else:
            product["flow"]["profile"] = profile
        documents["parts/TST-0001/part.yaml"]["process"] = process
        fx.write_record(self.hw, documents)

    def check(self, *stage: str) -> tuple[int, dict[str, Any]]:
        result = run("check", "--hw", str(self.hw), *(("--stage", *stage) if stage else ()))
        return result.returncode, json.loads(result.stdout) if result.stdout else {"stderr": result.stderr}

    def test_a_complete_profile_with_its_decision_passes_at_every_stage(self) -> None:
        self.write("release", fx.flow_profile())
        self.assertEqual(self.check(), (0, {"ok": True, "stage": "release", "problems": []}))

    def test_a_missing_profile_blocks_from_specify_on(self) -> None:
        for stage, code in (("intent", 0), ("specify", 1), ("layout", 1)):
            with self.subTest(stage=stage):
                self.write(stage)
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]], ["flow/profile"] if code else [])

    def test_a_profile_without_its_decision_blocks(self) -> None:
        no_decision = fx.flow_profile()
        del no_decision["decision"]
        for name, profile in (("no decision", no_decision), ("no decision record", fx.flow_profile("DR-009"))):
            with self.subTest(name):
                self.write("specify", profile)
                exit_code, report = self.check()
                self.assertEqual(exit_code, 1, report)
                self.assertEqual([p["path"] for p in report["problems"]], ["flow/profile/decision"])

    def test_a_not_asked_field_blocks_once_its_check_falls_due_and_not_before(self) -> None:
        self.write("parts", fx.flow_profile(fea="not_asked"))
        for stage, code in (("detail", 0), ("analysis", 1), ("release", 1)):
            with self.subTest(stage=stage):
                exit_code, report = self.check(stage)
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]], ["flow/profile/fea"] if code else [])

    def test_the_fdm_questions_fall_due_only_for_a_product_that_plans_fdm(self) -> None:
        profile = fx.flow_profile(fdm={"orientation": "not_asked"})
        for process, code in (("fdm", 1), ("cnc", 0)):
            with self.subTest(process=process):
                self.write("parts", profile, process=process)
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]],
                                 ["flow/profile/processes/fdm/orientation"] if code else [])

    def test_the_pip_clearance_falls_due_only_while_print_in_place_is_offered(self) -> None:
        for offer, code in (("offer", 1), ("no", 0)):
            with self.subTest(print_in_place=offer):
                self.write("detail", fx.flow_profile(fdm={"print_in_place": offer,
                                                          "pip_clearance_mm": {"xy": 0.3, "z": "not_asked"}}))
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]],
                                 ["flow/profile/processes/fdm/pip_clearance_mm/z"] if code else [])

    def test_an_unquoted_on_blocks_with_the_reason(self) -> None:
        self.write("specify", fx.flow_profile(fea=True))
        exit_code, report = self.check()
        self.assertEqual(exit_code, 1, report)
        self.assertEqual([p["path"] for p in report["problems"]], ["flow/profile/fea"])
        self.assertIn("quote", report["problems"][0]["message"])

    def test_check_exits_2_without_a_readable_record_or_with_an_unknown_stage(self) -> None:
        self.write("parts", fx.flow_profile())
        self.assertEqual(run("check", "--hw", str(self.hw), "--stage", "assembly").returncode, 2)
        (self.hw / "product.yaml").unlink()
        self.assertEqual(run("check", "--hw", str(self.hw)).returncode, 2)


class Conflicts(unittest.TestCase):
    """Design section 4: what validate.py reports as flow.conflict, one rule per test."""

    def setUp(self) -> None:
        self.documents = fx.tst_record()

    def conflicts(self, fea_cases: tuple[str, ...] = (), **profile: Any) -> list[tuple[str, bool]]:
        product = self.documents["product.yaml"]
        product["flow"]["profile"] = fx.flow_profile(**profile)
        parts = {rel.split("/")[1]: doc for rel, doc in self.documents.items() if rel.startswith("parts/")}
        found = flow_profile.conflicts(product, self.documents["requirements.yaml"], self.documents["interfaces.yaml"],
                                       parts, fea_cases)
        return [(item.rule, item.refuse) for item in found]

    def pip_joint(self, **interface: Any) -> None:
        joint = {"joint": "hinge", "print_pose": {"J-001": 0}, "break_free": {"method": "flex by hand"},
                 "gap_features": [[{"PN": "TST-0001", "feature": "knuckle"}, {"PN": "TST-0002", "feature": "pin"}]]}
        self.documents["interfaces.yaml"]["interfaces"][0]["functional"]["print_in_place"] = [joint]
        self.documents["interfaces.yaml"]["interfaces"][0].update(interface)

    def test_the_fixture_profile_conflicts_with_nothing_in_the_fixture_record(self) -> None:
        self.assertEqual(self.conflicts(), [])

    def test_fea_off_conflicts_with_a_strength_requirement_or_an_authored_fea_case(self) -> None:
        self.assertEqual(self.conflicts(fea="off", critique="light", fea_cases=("bracket_load",)), [("fea_off", False)])
        self.documents["requirements.yaml"]["requirements"][0]["verification"]["step"] = "fea"
        self.assertEqual(self.conflicts(fea="off", critique="light"), [("fea_off", False)])

    def test_stack_up_off_conflicts_with_a_critical_interface(self) -> None:
        self.assertEqual(self.conflicts(stack_up="off"), [("stack_up_off", False)])

    def test_fea_on_conflicts_with_a_light_or_no_critique(self) -> None:
        for critique in ("light", "off"):
            with self.subTest(critique=critique):
                self.assertEqual(self.conflicts(critique=critique), [("fea_without_critique", False)])

    def test_support_free_printing_conflicts_with_a_part_that_plans_supports(self) -> None:
        self.assertEqual(self.conflicts(fdm={"supports": "free"}), [("supports_free", False)])

    def test_a_print_in_place_joint_conflicts_with_declining_print_in_place_or_an_unchecked_overhang(self) -> None:
        self.pip_joint()
        for fdm in ({"print_in_place": "no"}, {"overhang_deg": "off"}):
            with self.subTest(**fdm):
                self.assertEqual(self.conflicts(fdm=fdm), [("pip_not_offered", False)])

    def test_a_print_in_place_joint_conflicts_with_a_required_play_tighter_than_the_clearance(self) -> None:
        self.pip_joint(pair=["TST-0001", "TST-0002"], min_clearance_mm=0.2, type="clearance")
        self.assertEqual(self.conflicts(fdm={"pip_clearance_mm": {"xy": 0.2, "z": "calibrated"}}), [])
        self.assertEqual(self.conflicts(fdm={"pip_clearance_mm": {"xy": 0.2, "z": 0.3}}), [("pip_play_too_tight", False)])

    def test_safety_critical_service_or_regulated_rigor_refuses_every_off_and_light(self) -> None:
        profile = {"fea": "off", "critique": "light", "stack_up": "auto", "fdm": {"warp": "off"}}
        expected = [("fea_off", False), ("safety_off", True), ("safety_off", True), ("safety_off", True)]
        self.documents["product.yaml"]["use_case"]["service_class"] = fx.known("safety_critical")
        self.assertEqual(self.conflicts(fea_cases=("bracket_load",), **profile), expected)
        self.documents["product.yaml"]["use_case"]["service_class"] = fx.known("production")
        self.documents["product.yaml"]["rigor"] = "regulated"
        self.assertEqual(self.conflicts(fea_cases=("bracket_load",), **profile), expected)


if __name__ == "__main__":
    unittest.main()
