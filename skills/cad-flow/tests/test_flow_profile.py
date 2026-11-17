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
yaml_loader = flow_profile.yaml_loader  # the repo's YAML reader: YAML 1.1, so a bare on, off, yes or no is a boolean
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

OPTIONS = ("human-approval", "fea", "stack-up", "critique", "results-in-git", "orientation", "supports", "overhang-deg",
           "bridge-mm", "min-wall-mm", "min-feature-mm", "warp", "cosmetic-faces", "print-in-place", "pip-clearance-xy",
           "pip-clearance-z")
QUESTION_FIELDS = fx.PROFILE_QUESTIONS

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

    def test_flow_gates_take_gated_stage_keys_and_an_approval_cites_its_decision(self) -> None:
        validator = flow_profile.schema_registry.ref_validator(
            flow_profile.SCHEMAS, "https://srobroek.com/cad/schemas/product.schema.json#/$defs/gates")
        version = "v3@" + "a" * 64
        self.assertEqual(list(validator.iter_errors({"intent": {"state": "approved", "decision": "DR-001"},
                                                     "layout": {"state": "provisional", "version": version}})), [])
        for name, gates in (("bad key", {"specify": {"state": "provisional"}}),
                            ("bad state", {"layout": {"state": "void", "version": version}}),
                            ("approved without decision", {"layout": {"state": "approved", "version": version}})):
            with self.subTest(name):
                self.assertNotEqual(list(validator.iter_errors(gates)), [])

    def test_the_fea_shape_agreed_with_package_a(self) -> None:
        validator = flow_profile.schema_registry.ref_validator(flow_profile.SCHEMAS, flow_profile.PROFILE_REF)
        accepted = ({"enabled": False}, {"enabled": True, "mode": "accurate"}, {"enabled": True, "mode": "fast"},
                    {"enabled": True, "mode": "fast", "fast_band_pct": 50})
        rejected = ({"enabled": True}, {"enabled": False, "mode": "fast"}, {"enabled": False, "fast_band_pct": 5},
                    {"enabled": True, "mode": "accurate", "fast_band_pct": 5},
                    {"enabled": True, "mode": "fast", "fast_band_pct": 0},
                    {"enabled": True, "mode": "fast", "fast_band_pct": -1},
                    {"enabled": True, "mode": "fast", "fast_band_pct": 50.5},
                    {"enabled": True, "mode": "fast", "solver": "ccx"}, "off", True, "not_asked")
        for fea in accepted:
            with self.subTest(accepted=fea):
                self.assertEqual(list(validator.iter_errors(fx.flow_profile(fea=fea))), [])
        for fea in rejected:
            with self.subTest(rejected=fea):
                self.assertNotEqual(list(validator.iter_errors(fx.flow_profile(fea=fea))), [])

    def test_a_fea_setting_needs_a_boolean_enabled_and_is_otherwise_not_assessed(self) -> None:
        self.assertEqual(flow_profile.fea_setting(fx.flow_profile(fea="off")), (None, None))
        self.assertEqual(flow_profile.fea_setting(fx.flow_profile(fea={"enabled": False})), (False, None))
        self.assertEqual(flow_profile.fea_setting(fx.flow_profile(fea={"enabled": True, "mode": "fast"})), (True, "fast"))

    def test_the_schema_has_no_not_asked_value(self) -> None:
        self.assertNotIn("not_asked", (SKILLS / "cad-record" / "schemas" / "product.schema.json").read_text(encoding="utf-8"))

    def test_every_question_field_and_motion_has_a_schema_default_that_flow_profile_applies(self) -> None:
        schema = json.loads((SKILLS / "cad-record" / "schemas" / "product.schema.json").read_text(encoding="utf-8"))["$defs"]
        profile = schema["profile"]["properties"]
        fdm = schema["fdm_profile"]["properties"]
        fea = schema["fea"]["properties"]
        expected = {
            "human_approval": profile["human_approval"]["default"], "stack_up": profile["stack_up"]["default"],
            "critique": profile["critique"]["default"], "motion": profile["motion"]["default"],
            "storage_results_in_git": profile["storage_results_in_git"]["default"],
            "fea": {"enabled": fea["enabled"]["default"], "mode": fea["mode"]["default"]},
            **{f"processes/fdm/{key}": fdm[key]["default"] for key in (
                "orientation", "supports", "overhang_deg", "bridge_mm", "min_wall_mm", "min_feature_mm", "warp",
                "cosmetic_faces", "print_in_place")},
            **{f"processes/fdm/pip_clearance_mm/{axis}": fdm["pip_clearance_mm"]["properties"][axis]["default"]
               for axis in ("xy", "z")}}
        self.assertEqual(flow_profile.DEFAULTS, expected)
        self.assertEqual(set(flow_profile.DEFAULTS), {*flow_profile.QUESTION_FIELDS, "motion"})
        validator = flow_profile.schema_registry.ref_validator(flow_profile.SCHEMAS, flow_profile.PROFILE_REF)
        self.assertEqual(list(validator.iter_errors(flow_profile.defaulted_profile("DR-001"))), [])

    def test_the_asked_state_takes_only_the_two_provenances(self) -> None:
        validator = flow_profile.schema_registry.ref_validator(flow_profile.SCHEMAS, flow_profile.PROFILE_REF)
        for provenance, ok in (("user", True), ("asked-accepted-default", True), ("not_asked", False), ("default", False)):
            with self.subTest(provenance):
                errors = list(validator.iter_errors(fx.flow_profile(asked={"stack_up": provenance})))
                self.assertEqual(errors == [], ok)

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

    def test_questions_lists_the_default_of_every_decision(self) -> None:
        lines = run("questions").stdout.splitlines()
        for line in ("  human_approval: \"all\"", "  fea: {\"enabled\": true, \"mode\": \"accurate\"}",
                     "  processes.fdm.print_in_place: false", "  processes.fdm.pip_clearance_mm.z: \"calibrated\"",
                     "  storage_results_in_git: false"):
            self.assertIn(line, lines)


class Record(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-flow-profile-")
        self.addCleanup(temporary.cleanup)
        self.hw = Path(temporary.name) / "hw"
        self.hw.mkdir()
        self.product = self.hw / "product.yaml"
        self.product.write_text(PRODUCT, encoding="utf-8")

    def record(self, decision: str = "DR-001", *answers: str) -> subprocess.CompletedProcess[str]:
        """The user answers the options in `answers` (option, value pairs) and accepts the default of every other."""
        given = {answers[i].removeprefix("--") for i in range(0, len(answers), 2)}
        accepted = [name for name in OPTIONS if name not in given]
        return run("record", "--hw", str(self.hw), "--decision", decision, *answers, "--accept-default", *accepted)

    def profile(self) -> Any:
        return yaml_loader.safe_load(self.product.read_text(encoding="utf-8"))["flow"]["profile"]

    def test_record_writes_the_answers_the_defaults_accepted_and_who_gave_each(self) -> None:
        result = self.record("DR-001", "--human-approval", "key", "--fea", "fast:5", "--stack-up", "off", "--critique", "light",
                             "--results-in-git", "no", "--supports", "free", "--overhang-deg", "50", "--warp", "off",
                             "--cosmetic-faces", "on", "--print-in-place", "offer", "--pip-clearance-xy", "0.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        c = "calibrated"
        accepted = ("orientation", "bridge_mm", "min_wall_mm", "min_feature_mm", "pip_clearance_mm/z")
        self.assertEqual(self.profile(), {
            "decision": "DR-001", "human_approval": "key", "fea": {"enabled": True, "mode": "fast", "fast_band_pct": 5},
            "stack_up": "off", "critique": "light", "motion": "auto", "storage_results_in_git": False,
            "processes": {"fdm": {"orientation": "user", "supports": "free", "overhang_deg": 50, "bridge_mm": c, "min_wall_mm": c,
                                  "min_feature_mm": c, "warp": "off", "cosmetic_faces": True, "print_in_place": True,
                                  "pip_clearance_mm": {"xy": 0.3, "z": c}}},
            "asked": {field: ("asked-accepted-default" if field.removeprefix("processes/fdm/") in accepted else "user")
                      for field in QUESTION_FIELDS}})
        data = yaml_loader.safe_load(self.product.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in data.items() if k != "flow"},
                         {"record_version": 2, "id": "tst-product", "rigor": "pro", "units": "mm"})
        self.assertEqual((data["flow"]["stage"], data["flow"]["recheck_from"]), ("intent", "intent"))
        self.assertIn("# a comment the profile record keeps\n", self.product.read_text(encoding="utf-8"))

    def test_accepting_every_default_records_the_conservative_defaults(self) -> None:
        self.assertEqual(run("record", "--hw", str(self.hw), "--decision", "DR-001", "--accept-default", *OPTIONS).returncode, 0)
        profile = self.profile()
        self.assertEqual({k: v for k, v in profile.items() if k != "asked"}, {
            "decision": "DR-001", "human_approval": "all", "fea": {"enabled": True, "mode": "accurate"}, "stack_up": "auto",
            "critique": "full", "motion": "auto", "storage_results_in_git": False,
            "processes": {"fdm": {"orientation": "user", "supports": "allowed", "overhang_deg": "calibrated",
                                  "bridge_mm": "calibrated", "min_wall_mm": "calibrated", "min_feature_mm": "calibrated",
                                  "warp": "calibrated", "cosmetic_faces": False, "print_in_place": False,
                                  "pip_clearance_mm": {"xy": "calibrated", "z": "calibrated"}}}})
        self.assertEqual(set(profile["asked"].values()), {"asked-accepted-default"})

    def test_record_refuses_a_question_neither_answered_nor_accepted_and_writes_nothing(self) -> None:
        result = run("record", "--hw", str(self.hw), "--decision", "DR-001", "--fea", "off", "--accept-default",
                     *[name for name in OPTIONS if name not in ("fea", "stack-up", "warp")])
        self.assertEqual(result.returncode, 2)
        self.assertIn("--stack-up", result.stderr)
        self.assertIn("--warp", result.stderr)
        self.assertNotIn("--critique", result.stderr)
        self.assertEqual(self.product.read_text(encoding="utf-8"), PRODUCT)
        self.assertFalse((self.hw / "decisions").exists())

    def test_record_refuses_an_option_both_answered_and_accepted(self) -> None:
        result = run("record", "--hw", str(self.hw), "--decision", "DR-001", "--fea", "off", "--accept-default", *OPTIONS)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--fea", result.stderr)
        self.assertEqual(self.product.read_text(encoding="utf-8"), PRODUCT)

    def test_record_quotes_every_string_it_writes(self) -> None:
        self.assertEqual(self.record("DR-001", "--stack-up", "off", "--warp", "off", "--fea", "off").returncode, 0)
        text = self.product.read_text(encoding="utf-8")
        for line in ('    stack_up: "off"\n', '        warp: "off"\n', '      enabled: false\n', '    critique: "full"\n'):
            self.assertIn(line, text)

    def test_the_fea_answer_records_off_accurate_or_fast_with_an_optional_band(self) -> None:
        for decision, answer, fea in (("DR-001", "off", {"enabled": False}),
                                      ("DR-002", "accurate", {"enabled": True, "mode": "accurate"}),
                                      ("DR-003", "fast", {"enabled": True, "mode": "fast"}),
                                      ("DR-004", "fast:2.5", {"enabled": True, "mode": "fast", "fast_band_pct": 2.5}),
                                      ("DR-005", "fast:50", {"enabled": True, "mode": "fast", "fast_band_pct": 50})):
            with self.subTest(answer):
                self.product.write_text(PRODUCT, encoding="utf-8")
                self.assertEqual(self.record(decision, "--fea", answer).returncode, 0)
                self.assertEqual(self.profile()["fea"], fea)
        for index, answer in enumerate(("on", "accurate:5", "fast:0", "fast:-1", "fast:50.5", "fast:x")):
            with self.subTest(answer):
                self.assertEqual(self.record(f"DR-09{index}", "--fea", answer).returncode, 2)

    def test_record_writes_the_answers_as_an_approved_intent_decision(self) -> None:
        self.assertEqual(self.record("DR-004", "--fea", "accurate").returncode, 0)
        text = (self.hw / "decisions" / "DR-004.md").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[:3], ["Status: APPROVED", "Gate: intent", "Version: N/A"])
        self.assertIn("| fea.mode | accurate | user |", text)
        self.assertIn("| processes.fdm.orientation | user | asked-accepted-default |", text)
        self.assertIn("| motion | auto | stated |", text)

    def test_record_refuses_a_decision_that_exists_and_changes_nothing(self) -> None:
        (self.hw / "decisions").mkdir()
        (self.hw / "decisions" / "DR-001.md").write_text("# DR-001 an earlier decision\n", encoding="utf-8")
        result = self.record("DR-001", "--fea", "accurate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("DR-001", result.stderr)
        self.assertEqual(self.product.read_text(encoding="utf-8"), PRODUCT)
        self.assertEqual((self.hw / "decisions" / "DR-001.md").read_text(encoding="utf-8"), "# DR-001 an earlier decision\n")

    def test_a_second_record_replaces_the_profile_under_its_new_decision(self) -> None:
        self.assertEqual(self.record("DR-001", "--fea", "off").returncode, 0)
        self.assertEqual(self.record("DR-002", "--fea", "accurate").returncode, 0)
        self.assertEqual((self.profile()["decision"], self.profile()["fea"]), ("DR-002", {"enabled": True, "mode": "accurate"}))
        self.assertEqual(self.product.read_text(encoding="utf-8").count("profile:"), 1)

    def test_record_refuses_a_flow_it_cannot_edit_as_text(self) -> None:
        text = PRODUCT.replace("flow:\n  stage: intent\n  recheck_from: intent\n", "flow: {stage: intent, recheck_from: intent}\n")
        self.product.write_text(text, encoding="utf-8")
        result = self.record("DR-001", "--fea", "accurate")
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

    def test_a_profile_without_its_approving_decision_blocks(self) -> None:
        no_decision = fx.flow_profile()
        del no_decision["decision"]
        record = self.hw / "decisions" / "DR-009.md"
        for name, profile, text in (("no decision", no_decision, None), ("no decision record", fx.flow_profile("DR-009"), None),
                                    ("not an approval", fx.flow_profile("DR-009"), "Status: PROVISIONAL\n\n# DR-009\n"),
                                    ("no status", fx.flow_profile("DR-009"), "# DR-009\n\nThe user approved.\n")):
            with self.subTest(name):
                self.write("specify", profile)
                record.unlink(missing_ok=True)
                if text is not None:
                    record.write_text(text, encoding="utf-8")
                exit_code, report = self.check()
                self.assertEqual(exit_code, 1, report)
                self.assertEqual([p["path"] for p in report["problems"]], ["flow/profile/decision"])

    def test_a_profile_with_no_asked_state_blocks_on_every_start_question_from_specify_on(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]
        for stage, code in (("intent", 0), ("specify", 1), ("release", 1)):
            with self.subTest(stage=stage):
                self.write(stage, profile)
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]],
                                 [f"flow/profile/asked/{field}" for field in QUESTION_FIELDS] if code else [])
                if code:
                    self.assertIn("was not asked", report["problems"][0]["message"])

    def test_one_question_with_no_asked_state_blocks_by_itself(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]["fea"]
        self.write("parts", profile)
        exit_code, report = self.check()
        self.assertEqual((exit_code, [p["path"] for p in report["problems"]]), (1, ["flow/profile/asked/fea"]))

    def test_an_unknown_provenance_is_a_schema_finding(self) -> None:
        profile = fx.flow_profile(asked={**fx.flow_profile()["asked"], "critique": "not_asked"})
        self.write("parts", profile)
        exit_code, report = self.check()
        self.assertEqual(exit_code, 1, report)
        self.assertIn("flow/profile/asked/critique", [p["path"] for p in report["problems"]])

    def test_a_default_marked_accepted_must_equal_the_default(self) -> None:
        profile = fx.flow_profile(stack_up="off")
        profile["asked"]["stack_up"] = "asked-accepted-default"
        self.write("parts", profile)
        exit_code, report = self.check()
        self.assertEqual((exit_code, [p["path"] for p in report["problems"]]), (1, ["flow/profile/asked/stack_up"]))
        self.assertIn("auto", report["problems"][0]["message"])
        profile = fx.flow_profile(stack_up="auto")
        profile["asked"]["stack_up"] = "asked-accepted-default"
        self.write("parts", profile)
        self.assertEqual(self.check()[0], 0)

    def test_the_fdm_questions_apply_only_to_a_product_that_plans_fdm(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]["processes/fdm/orientation"]
        for process, code in (("fdm", 1), ("cnc", 0)):
            with self.subTest(process=process):
                self.write("parts", profile, process=process)
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]],
                                 ["flow/profile/asked/processes/fdm/orientation"] if code else [])

    def test_the_pip_clearance_questions_apply_only_while_print_in_place_is_offered(self) -> None:
        for offer, code in ((True, 1), (False, 0)):
            with self.subTest(print_in_place=offer):
                profile = fx.flow_profile(fdm={"print_in_place": offer})
                del profile["asked"]["processes/fdm/pip_clearance_mm/z"]
                self.write("detail", profile)
                exit_code, report = self.check()
                self.assertEqual(exit_code, code, report)
                self.assertEqual([p["path"] for p in report["problems"]],
                                 ["flow/profile/asked/processes/fdm/pip_clearance_mm/z"] if code else [])

    def test_check_exits_2_without_a_readable_record_or_with_an_unknown_stage(self) -> None:
        self.write("parts", fx.flow_profile())
        self.assertEqual(run("check", "--hw", str(self.hw), "--stage", "assembly").returncode, 2)
        (self.hw / "product.yaml").unlink()
        self.assertEqual(run("check", "--hw", str(self.hw)).returncode, 2)


class BareYaml(unittest.TestCase):
    """YAML 1.1 reads a bare on, off, yes or no as a boolean (the repo's yaml_loader). A two-state field is a
    boolean, so the bare word is its value; a field with more states is a string, and a bare word blocks
    with flow.profile_unquoted, never read as an answer."""

    BOOLEAN = (("enabled", "on", "fea/enabled", True), ("enabled", "off", "fea/enabled", False),
               ("cosmetic_faces", "on", "processes/fdm/cosmetic_faces", True),
               ("cosmetic_faces", "off", "processes/fdm/cosmetic_faces", False),
               ("print_in_place", "yes", "processes/fdm/print_in_place", True),
               ("print_in_place", "no", "processes/fdm/print_in_place", False),
               ("storage_results_in_git", "yes", "storage_results_in_git", True),
               ("storage_results_in_git", "no", "storage_results_in_git", False))
    STRING = (("stack_up", "on", "stack_up"), ("stack_up", "off", "stack_up"), ("critique", "off", "critique"),
              ("supports", "off", "processes/fdm/supports"), ("overhang_deg", "off", "processes/fdm/overhang_deg"),
              ("bridge_mm", "off", "processes/fdm/bridge_mm"), ("min_wall_mm", "off", "processes/fdm/min_wall_mm"),
              ("min_feature_mm", "off", "processes/fdm/min_feature_mm"), ("warp", "off", "processes/fdm/warp"))

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-flow-profile-bare-")
        self.addCleanup(temporary.cleanup)
        self.hw = fx.write_record(Path(temporary.name) / "hw", fx.record())

    def parsed(self, key: str, word: str) -> Any:
        """product.yaml with `key: <word>` written bare, read by the repo's loader."""
        product = fx.product(stage="release")
        if key == "enabled":
            product["flow"]["profile"]["fea"] = {"enabled": True, "mode": "accurate"} if word == "on" else {"enabled": False}
        text = yaml.safe_dump(product, sort_keys=False)
        lines = [line for line in text.splitlines() if line.lstrip().startswith(f"{key}:") and not line.endswith(": user")]  # not its asked key
        self.assertEqual(len(lines), 1, key)
        indent = lines[0][:len(lines[0]) - len(lines[0].lstrip())]
        return yaml_loader.safe_load(text.replace(lines[0], f"{indent}{key}: {word}"))

    def test_a_bare_word_is_the_value_of_a_two_state_field(self) -> None:
        for key, word, path, value in self.BOOLEAN:
            with self.subTest(f"{key}: {word}"):
                product = self.parsed(key, word)
                self.assertEqual(flow_profile.value_at(product["flow"]["profile"], path), value)
                self.assertEqual(flow_profile.gate(self.hw, "release", product=product)[1], [])

    def test_a_bare_on_or_off_in_a_field_with_more_states_blocks_and_asks_for_quotes(self) -> None:
        for key, word, path in self.STRING:
            with self.subTest(f"{key}: {word}"):
                _stage, found = flow_profile.gate(self.hw, "release", product=self.parsed(key, word))
                self.assertEqual([(item.code, item.path) for item in found], [("flow.profile_unquoted", f"flow/profile/{path}")])
                self.assertIn(f'write "{word}" in quotes', found[0].message)


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
        self.assertEqual(self.conflicts(fea={"enabled": False}, critique="light", fea_cases=("bracket_load",)),
                         [("fea_off", False)])
        self.documents["requirements.yaml"]["requirements"][0]["verification"]["step"] = "fea"
        self.assertEqual(self.conflicts(fea={"enabled": False}, critique="light"), [("fea_off", False)])

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
        for fdm in ({"print_in_place": False}, {"overhang_deg": "off"}):
            with self.subTest(**fdm):
                self.assertEqual(self.conflicts(fdm=fdm), [("pip_not_offered", False)])

    def test_a_print_in_place_joint_conflicts_with_a_required_play_tighter_than_the_clearance(self) -> None:
        self.pip_joint(pair=["TST-0001", "TST-0002"], min_clearance_mm=0.2, type="clearance")
        self.assertEqual(self.conflicts(fdm={"pip_clearance_mm": {"xy": 0.2, "z": "calibrated"}}), [])
        self.assertEqual(self.conflicts(fdm={"pip_clearance_mm": {"xy": 0.2, "z": 0.3}}), [("pip_play_too_tight", False)])

    def test_safety_critical_service_or_regulated_rigor_refuses_every_off_and_light(self) -> None:
        profile = {"fea": {"enabled": False}, "critique": "light", "stack_up": "auto", "fdm": {"warp": "off"}}
        expected = [("fea_off", False), ("safety_off", True), ("safety_off", True), ("safety_off", True)]
        self.documents["product.yaml"]["use_case"]["service_class"] = fx.known("safety_critical")
        self.assertEqual(self.conflicts(fea_cases=("bracket_load",), **profile), expected)
        self.documents["product.yaml"]["use_case"]["service_class"] = fx.known("production")
        self.documents["product.yaml"]["rigor"] = "regulated"
        self.assertEqual(self.conflicts(fea_cases=("bracket_load",), **profile), expected)


if __name__ == "__main__":
    unittest.main()
