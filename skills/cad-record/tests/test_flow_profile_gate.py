#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""flow_profile_gate: the pure gate on product.yaml flow.profile (missing or unasked answers, unquoted words,
the schema's shape) and the flow.conflict rules between the profile and the rest of the record. The gate
reads no files: every case passes the parsed record and an approval callable."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL.parents[1] / "scripts"))
from modload import load_module  # noqa: E402 - the path to scripts/ is put on sys.path just above

gate_module = load_module("cad_record_flow_profile_gate", SKILL / "scripts" / "flow_profile_gate.py")
yaml_loader = load_module("cad_record_yaml_loader", SKILL / "scripts" / "yaml_loader.py")
fx = load_module("cad_record_mi_fixtures", HERE / "fixtures" / "manufacturing_intent.py")

PRODUCT_SCHEMA = json.loads((SKILL / "schemas" / "product.schema.json").read_text(encoding="utf-8"))
INTERFACES = "https://srobroek.com/cad/schemas/interfaces.schema.json"
# The project-start answers' fields, written out here apart from the module's own list.
QUESTION_FIELDS = (
    "human_approval", "fea", "stack_up", "critique", "storage_results_in_git", "processes/fdm/orientation",
    "processes/fdm/supports", "processes/fdm/overhang_deg", "processes/fdm/bridge_mm", "processes/fdm/min_wall_mm",
    "processes/fdm/min_feature_mm", "processes/fdm/warp", "processes/fdm/cosmetic_faces", "processes/fdm/print_in_place",
    "processes/fdm/pip_clearance_mm/xy", "processes/fdm/pip_clearance_mm/z")


UNKNOWN_ROUTE = {"status": "unknown", "reason": "not planned yet"}
NOT_APPLICABLE = {"status": "not_applicable", "reason": "no FDM process is planned", "decision": "DR-001"}


def approved(_decision: str) -> None:
    return None


def refused(why: str) -> Any:
    return lambda _decision: why


def profile_errors(profile: Any) -> list[str]:
    validator = gate_module.schema_registry.ref_validator(gate_module.SCHEMAS, gate_module.PROFILE_REF)
    return [error.message for error in validator.iter_errors(profile)]


class SchemaContract(unittest.TestCase):
    """The product.schema.json values other packages build on."""

    def test_the_question_fields_are_the_sixteen_answers_in_question_order(self) -> None:
        self.assertEqual(gate_module.QUESTION_FIELDS, QUESTION_FIELDS)

    def test_every_question_field_and_motion_has_a_schema_default_that_the_module_applies(self) -> None:
        defs = PRODUCT_SCHEMA["$defs"]
        profile, fdm, fea = defs["profile"]["properties"], defs["fdm_profile"]["properties"], defs["fea"]["properties"]
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
        self.assertEqual(gate_module.DEFAULTS, expected)
        self.assertEqual(set(gate_module.DEFAULTS), {*QUESTION_FIELDS, "motion"})

    def test_the_fea_shape_agreed_with_package_a(self) -> None:
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
                self.assertEqual(profile_errors(fx.flow_profile(fea=fea)), [])
        for fea in rejected:
            with self.subTest(rejected=fea):
                self.assertNotEqual(profile_errors(fx.flow_profile(fea=fea)), [])

    def test_a_fea_setting_needs_a_boolean_enabled_and_is_otherwise_not_assessed(self) -> None:
        for fea, expected in (("off", (None, None)), ({"enabled": False}, (False, None)),
                              ({"enabled": True, "mode": "fast"}, (True, "fast"))):
            with self.subTest(fea=fea):
                self.assertEqual(gate_module.fea_setting(fx.flow_profile(fea=fea)), expected)

    def test_the_asked_state_takes_only_the_two_provenances(self) -> None:
        for provenance, ok in (("user", True), ("asked-accepted-default", True), ("not_asked", False), ("default", False)):
            with self.subTest(provenance):
                self.assertEqual(profile_errors(fx.flow_profile(asked={"stack_up": provenance})) == [], ok)

    def test_fdm_is_the_iso_52900_material_extrusion_category(self) -> None:
        processes = PRODUCT_SCHEMA["$defs"]["profile"]["properties"]["processes"]["properties"]
        self.assertEqual(gate_module.ISO_52900_CATEGORY["fdm"], "mex")
        self.assertEqual(set(gate_module.ISO_52900_CATEGORY), set(processes), "every profile process has a category")
        self.assertLessEqual(set(gate_module.ISO_52900_CATEGORY.values()),
                             set(PRODUCT_SCHEMA["$defs"]["iso_52900_category"]["enum"]))

    def test_the_print_in_place_block_of_an_interface_names_joints_faces_and_how_they_break_free(self) -> None:
        face = {"PN": "TST-0001", "feature": "knuckle"}
        other = {"PN": "TST-0002", "feature": "pin"}
        joint = {"joint": "hinge", "print_pose": {"J-001": 0}, "gap_features": [[face, other]],
                 "break_free": {"method": "flex by hand", "force_n": 5}}
        validator = gate_module.schema_registry.ref_validator(
            gate_module.SCHEMAS, f"{INTERFACES}#/$defs/functional/properties/print_in_place")
        self.assertEqual(list(validator.iter_errors([joint])), [])
        for name, bad in (("unknown joint", {**joint, "joint": "gear"}), ("no gap", {**joint, "gap_features": []}),
                          ("one face", {**joint, "gap_features": [[face]]}),
                          ("bad joint id", {**joint, "print_pose": {"hinge": 0}}),
                          ("no method", {**joint, "break_free": {"force_n": 5}}),
                          ("zero force", {**joint, "break_free": {"method": "pry", "force_n": 0}})):
            with self.subTest(name):
                self.assertNotEqual(list(validator.iter_errors([bad])), [])

    def test_a_requirement_verification_names_the_step_that_verifies_it(self) -> None:
        validator = gate_module.schema_registry.document_validator(gate_module.SCHEMAS, "requirements.schema.json")
        record = fx.tst_record()["requirements.yaml"]
        self.assertEqual(list(validator.iter_errors(record)), [])
        for step, ok in (("fea", True), ("physical", True), ("welding", False)):
            with self.subTest(step):
                edited = copy.deepcopy(record)
                edited["requirements"][0]["verification"]["step"] = step
                self.assertEqual(list(validator.iter_errors(edited)) == [], ok)


class Gate(unittest.TestCase):
    """The check cad_model.py run, validate.py and the stage advance share (the later PR wires them)."""

    def setUp(self) -> None:
        self.record = fx.tst_record()
        self.product = self.record["product.yaml"]
        self.parts = {rel.split("/")[1]: doc for rel, doc in self.record.items() if rel.startswith("parts/")}

    def with_profile(self, profile: Any, stage: str = "specify") -> dict[str, Any]:
        product = copy.deepcopy(self.product)
        product["flow"].update(stage=stage, recheck_from=stage)
        if profile is None:
            product["flow"].pop("profile", None)
        else:
            product["flow"]["profile"] = profile
        return product

    def check(self, recorded: str, profile: Any, approval: Any = approved, **kwargs: Any) -> list[tuple[str, str]]:
        """The gate's findings for a product recorded at stage `recorded`."""
        _stage, found = gate_module.gate(self.with_profile(profile, recorded), self.parts, approval, **kwargs)
        return [(item.code, item.path) for item in found]

    def test_a_complete_profile_with_an_approving_decision_passes_at_every_stage(self) -> None:
        for stage in gate_module.STAGES:
            with self.subTest(stage=stage):
                self.assertEqual(self.check(stage, fx.flow_profile()), [])

    def test_the_recovery_for_a_missing_profile_points_at_the_scoped_questions_command(self) -> None:
        _stage, found = gate_module.gate(self.with_profile(None, "specify"), self.parts, approved)
        self.assertIn("flow_profile.py questions --hw hw", found[0].action)

    def test_a_missing_profile_blocks_from_specify_on(self) -> None:
        for stage, expected in (("ground", []), ("intent", []), ("specify", [("flow.profile", "flow/profile")]),
                                ("layout", [("flow.profile", "flow/profile")])):
            with self.subTest(stage=stage):
                self.assertEqual(self.check(stage, None), expected)

    def test_a_build_checks_at_layout_at_the_earliest(self) -> None:
        self.assertEqual(self.check("intent", None, build=True), [("flow.profile", "flow/profile")])
        self.assertEqual(self.check("intent", None), [])

    def test_the_stage_argument_overrides_the_recorded_stage(self) -> None:
        self.assertEqual(self.check("intent", None, stage="parts"), [("flow.profile", "flow/profile")])

    def test_a_profile_citing_no_decision_blocks(self) -> None:
        profile = fx.flow_profile()
        del profile["decision"]
        self.assertEqual(self.check("specify", profile), [("flow.profile", "flow/profile/decision")])

    def test_a_cited_decision_that_does_not_approve_blocks_with_the_approvals_reason(self) -> None:
        _stage, found = gate_module.gate(self.with_profile(fx.flow_profile("DR-009")), self.parts,
                                         refused("has status proposed, not the user's approval"))
        self.assertEqual([(item.path, item.message) for item in found], [(
            "flow/profile/decision", "flow.profile cites DR-009, which has status proposed, not the user's approval")])

    def test_the_approval_is_asked_about_the_cited_decision_only(self) -> None:
        asked: list[str] = []
        self.check("specify", fx.flow_profile("DR-042"), approval=lambda decision: asked.append(decision))
        self.assertEqual(asked, ["DR-042"])

    def test_a_profile_with_no_asked_state_blocks_on_every_start_question_from_specify_on(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]
        for stage, expected in (("intent", []), ("specify", [f"flow/profile/asked/{field}" for field in QUESTION_FIELDS])):
            with self.subTest(stage=stage):
                self.assertEqual([path for _code, path in self.check(stage, profile)], expected)

    def test_one_question_with_no_asked_state_blocks_by_itself(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]["fea"]
        self.assertEqual(self.check("parts", profile), [("flow.profile", "flow/profile/asked/fea")])

    def test_an_unknown_provenance_is_a_schema_finding(self) -> None:
        profile = fx.flow_profile(asked={**fx.flow_profile()["asked"], "critique": "not_asked"})
        self.assertIn("flow/profile/asked/critique", [path for _code, path in self.check("parts", profile)])

    def test_asked_keys_of_mixed_types_with_bad_provenance_are_findings_not_a_crash(self) -> None:
        """YAML reads `1: bogus` as an integer key beside the string keys; the findings still sort and report."""
        profile = fx.flow_profile(asked={**fx.flow_profile()["asked"], 1: "bogus", "fea": "bogus"})
        paths = [path for _code, path in self.check("parts", profile)]
        self.assertIn("flow/profile/asked/1", paths)
        self.assertIn("flow/profile/asked/fea", paths)

    def test_a_default_marked_accepted_must_equal_the_default(self) -> None:
        profile = fx.flow_profile(stack_up="off")
        profile["asked"]["stack_up"] = "asked-accepted-default"
        self.assertEqual(self.check("parts", profile), [("flow.profile", "flow/profile/asked/stack_up")])
        _stage, found = gate_module.gate(self.with_profile(profile), self.parts, approved)
        self.assertIn("auto", found[0].message)
        profile = fx.flow_profile(stack_up="auto")
        profile["asked"]["stack_up"] = "asked-accepted-default"
        self.assertEqual(self.check("parts", profile), [])

    def test_the_fdm_questions_apply_only_to_a_product_that_plans_fdm(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]["processes/fdm/orientation"]
        fdm_part, cnc_part = self.parts["TST-0001"], self.parts["TST-0002"]
        for name, parts, processes, expected in (
                ("an fdm part", {"TST-0001": fdm_part}, None, 1), ("no fdm part", {"TST-0002": cnc_part}, None, 0),
                ("fdm planned at intent", {}, ["fdm"], 1), ("no parts and no plan", {}, None, 2),
                ("a plan without fdm and no parts", {}, ["cnc"], 0),
                ("a part whose route is unknown", {"A": {"process": "cnc", "intent": {"route": UNKNOWN_ROUTE}}}, None, 2),
                ("an fdm route operation", {"TST-0009": {"process": "cnc", "intent": {"route": [{"id": "print", "kind": "fdm", "capability": UNKNOWN_ROUTE}]}}}, None, 1)):
            with self.subTest(name):
                self.parts = parts
                product = self.with_profile(profile, "parts")
                if processes:
                    product["manufacturing"] = {"processes": processes}
                _stage, found = gate_module.gate(product, parts, approved)
                self.assertEqual(len(found), expected)

    def test_fdm_applicability_separates_unknown_from_known_non_fdm(self) -> None:
        cnc = {"process": "cnc", "intent": {"route": [{"id": "mill", "kind": "cnc", "capability": UNKNOWN_ROUTE}]}}
        for name, parts, processes, state, field in (
                ("no plan and no part", {}, None, "unknown", "manufacturing/processes"),
                ("a route that is unknown", {"A": {"process": "cnc", "intent": {"route": UNKNOWN_ROUTE}}}, None, "unknown",
                 "parts/A/intent/route"),
                ("a plan without fdm", {}, ["cnc"], "no", None), ("a known cnc part", {"A": cnc}, None, "no", None),
                ("an fdm plan", {}, ["fdm"], "yes", None),
                ("an fdm part beside an unknown route", {"A": {"process": "fdm"}, "B": {"intent": {"route": UNKNOWN_ROUTE}}},
                 None, "yes", None)):
            with self.subTest(name):
                product = {"manufacturing": {"processes": processes}} if processes else {}
                found = gate_module.fdm_applicability(product, parts)
                self.assertEqual(found.state, state)
                self.assertEqual([item.path for item in found.unresolved], [field] if field else [])

    def test_a_malformed_plan_or_part_record_is_a_finding_that_names_it_and_never_rules_fdm_out(self) -> None:
        for name, parts, processes, field in (("a plan that is a word", {}, "fdm", "manufacturing/processes"),
                                              ("an empty plan", {}, [], "manufacturing/processes"),
                                              ("a plan of numbers", {}, [1], "manufacturing/processes"),
                                              ("a process name that does not exist", {}, ["fmd"], "manufacturing/processes"),
                                              ("an explicit null", {}, None, "manufacturing/processes"),
                                              ("a part record that did not parse", {"A": None}, ["cnc"], "parts/A/part.yaml"),
                                              ("a part record that is a list", {"A": []}, ["cnc"], "parts/A/part.yaml")):
            with self.subTest(name):
                product = {"manufacturing": {"processes": processes}}
                found = gate_module.fdm_applicability(product, parts)
                self.assertEqual((found.state, [item.path for item in found.malformed]), ("unknown", [field]))
                self.assertEqual(found.malformed[0].code, gate_module.APPLICABILITY)

    def test_a_malformed_plan_is_reported_even_while_the_profile_is_missing(self) -> None:
        product = self.with_profile(None, "specify")
        product["manufacturing"] = {"processes": ["fmd"]}
        _stage, found = gate_module.gate(product, self.parts, approved)
        self.assertEqual([item.path for item in found], ["flow/profile", "manufacturing/processes"])
        self.assertIn("fmd", found[1].message)

    def test_malformed_applicability_inputs_are_reported_before_the_profile_is_due(self) -> None:
        for stage in ("ground", "intent"):
            for name, processes in (("a misspelled process", ["fmd"]), ("an explicit null", None), ("a scalar plan", "fdm")):
                with self.subTest(stage=stage, plan=name):
                    product = self.with_profile(None, stage)
                    product["manufacturing"] = {"processes": processes}
                    _stage, found = gate_module.gate(product, self.parts, approved)
                    self.assertEqual([(item.code, item.path) for item in found],
                                     [("flow.applicability", "manufacturing/processes")])
        clean = self.with_profile(None, "intent")
        clean["manufacturing"] = {"processes": ["cnc"]}
        self.assertEqual(gate_module.gate(clean, self.parts, approved)[1], [])

    def test_inputs_that_do_not_fit_their_schema_never_rule_fdm_out(self) -> None:
        unknown = {"status": "unknown", "reason": "not planned"}
        for name, product, parts, field in (
                ("a duplicate plan entry", {"manufacturing": {"processes": ["cnc", "cnc"]}}, {}, "manufacturing/processes"),
                ("a manufacturing block that is a list", {"manufacturing": ["cnc"]}, {}, "manufacturing"),
                ("a null manufacturing block", {"manufacturing": None}, {}, "manufacturing"),
                ("a part process that does not exist", {}, {"A": {"process": "fmd"}}, "parts/A/process"),
                ("a part process that is a list", {}, {"A": {"process": ["fdm"]}}, "parts/A/process"),
                ("an intent that is a string", {}, {"A": {"process": "cnc", "intent": "route"}}, "parts/A/intent"),
                ("a route that is a string", {}, {"A": {"process": "cnc", "intent": {"route": "cnc"}}}, "parts/A/intent/route"),
                ("an empty route", {}, {"A": {"process": "cnc", "intent": {"route": []}}}, "parts/A/intent/route"),
                ("a route entry that is not a mapping", {}, {"A": {"process": "cnc", "intent": {"route": ["cnc"]}}},
                 "parts/A/intent/route"),
                ("a route fact with another status", {}, {"A": {"process": "cnc", "intent": {"route": {
                    **unknown, "status": "known"}}}}, "parts/A/intent/route")):
            with self.subTest(name):
                base = {"manufacturing": {"processes": ["cnc"]}} if not product else {}
                found = gate_module.fdm_applicability({**base, **product}, parts)
                self.assertEqual((found.state, [item.path for item in found.malformed]), ("unknown", [field]))
                self.assertTrue(found.asks)

    def test_a_route_that_fails_the_part_schema_is_a_finding_at_every_stage_and_never_rules_fdm_out(self) -> None:
        cap = {"status": "unknown", "reason": "no profile yet"}
        for name, route in (("an upper-case operation kind", [{"id": "print", "kind": "FDM", "capability": cap}]),
                            ("an operation without an id", [{"kind": "cnc", "capability": cap}]),
                            ("a made operation without a capability", [{"id": "mill", "kind": "cnc"}]),
                            ("an unknown route without a reason", {"status": "unknown"})):
            for stage in ("ground", "intent", "specify"):
                with self.subTest(name, stage=stage):
                    parts = {"A": {"process": "cnc", "intent": {"route": route}}}
                    product = self.with_profile(fx.flow_profile() if stage == "specify" else None, stage)
                    product["manufacturing"] = {"processes": ["cnc"]}
                    _stage, found = gate_module.gate(product, parts, approved)
                    self.assertEqual([(item.code, item.path) for item in found],
                                     [("flow.applicability", "parts/A/intent/route")])
                    self.assertTrue(gate_module.fdm_applicability(product, parts).asks)

    def test_the_gate_blocks_on_a_malformed_plan_and_names_the_field(self) -> None:
        product = self.with_profile(fx.flow_profile(), "specify")
        product["manufacturing"] = {"processes": "fdm"}
        _stage, found = gate_module.gate(product, self.parts, approved)
        self.assertEqual([(item.code, item.path) for item in found], [("flow.applicability", "manufacturing/processes")])

    def test_a_not_applicable_disposition_is_no_answer_and_the_gate_asks_when_the_question_applies_again(self) -> None:
        fdm_fields = QUESTION_FIELDS[5:]
        asked = {field: ("user" if field not in fdm_fields else NOT_APPLICABLE) for field in QUESTION_FIELDS}
        profile = fx.flow_profile(asked=asked)
        self.assertEqual(profile_errors(profile), [])
        cnc, fdm = {"A": {"process": "cnc"}}, {"A": {"process": "fdm"}}
        self.assertEqual(gate_module.gate(self.with_profile(profile, "parts"), cnc, approved)[1], [])
        found = gate_module.gate(self.with_profile(profile, "parts"), fdm, approved)[1]
        self.assertEqual([item.path for item in found], [f"flow/profile/asked/{field}" for field in fdm_fields])
        self.assertIn("applies", found[0].message)

    def test_a_not_applicable_disposition_needs_its_reason(self) -> None:
        profile = fx.flow_profile()
        profile["asked"]["processes/fdm/orientation"] = {"status": "not_applicable"}
        self.assertNotEqual(profile_errors(profile), [])

    def test_the_question_plan_names_why_each_skipped_question_is_skipped(self) -> None:
        cnc = gate_module.question_plan({"manufacturing": {"processes": ["cnc"]}}, {})
        self.assertEqual(cnc.fields, list(QUESTION_FIELDS[:5]))
        self.assertEqual(list(cnc.skipped), list(QUESTION_FIELDS[5:]))
        self.assertTrue(all("no FDM process is planned" in reason for reason in cnc.skipped.values()), cnc.skipped)
        declined = gate_module.question_plan({"manufacturing": {"processes": ["fdm"]}}, {},
                                             {"processes": {"fdm": {"print_in_place": False}}})
        self.assertEqual(list(declined.skipped), list(QUESTION_FIELDS[14:]))
        self.assertTrue(all("print-in-place is declined" in reason for reason in declined.skipped.values()), declined.skipped)

    def test_an_unresolved_applicability_blocks_even_when_every_question_is_answered(self) -> None:
        for name, parts, field in (("no plan and no part", {}, "manufacturing/processes"),
                                   ("an unknown route", {"A": {"process": "cnc", "intent": {"route": UNKNOWN_ROUTE}}},
                                    "parts/A/intent/route")):
            with self.subTest(name):
                _stage, found = gate_module.gate(self.with_profile(fx.flow_profile(), "specify"), parts, approved)
                self.assertEqual([(item.code, item.path) for item in found], [("flow.applicability", field)])
                self.assertIn("cannot be ruled out", found[0].message)

    def test_an_unresolved_applicability_asks_the_fdm_questions_and_names_the_field(self) -> None:
        profile = fx.flow_profile()
        del profile["asked"]["processes/fdm/orientation"]
        for name, parts, field in (("no plan and no part", {}, "manufacturing/processes"),
                                   ("an unknown route", {"A": {"process": "cnc", "intent": {"route": UNKNOWN_ROUTE}}},
                                    "parts/A/intent/route")):
            with self.subTest(name):
                product = self.with_profile(profile, "parts")
                _stage, found = gate_module.gate(product, parts, approved)
                self.assertEqual([item.path for item in found], ["flow/profile/asked/processes/fdm/orientation", field])
                self.assertIn(field, found[0].message)

    def test_question_fields_is_the_set_of_questions_asked_problems_requires(self) -> None:
        fdm_part = {"process": "fdm", "intent": {"route": []}}
        cnc_part = {"process": "cnc", "intent": {"route": []}}
        for name, parts, offer in (("fdm, pip offered", {"A": fdm_part}, True), ("fdm, pip declined", {"A": fdm_part}, False),
                                   ("cnc only", {"A": cnc_part}, True), ("no parts", {}, False),
                                   ("unknown route", {"A": {"process": "cnc", "intent": {"route": UNKNOWN_ROUTE}}}, True)):
            with self.subTest(name):
                profile = fx.flow_profile(fdm={"print_in_place": offer})
                profile["asked"] = {}
                product = {"flow": {"profile": profile}}
                unasked = [problem.path.removeprefix("flow/profile/asked/")
                           for problem in gate_module.asked_problems(profile, fdm=gate_module.fdm_applicability(product, parts).asks)]
                self.assertEqual(gate_module.question_fields(product, parts), unasked)

    def test_question_fields_of_a_cnc_only_product_are_the_five_general_questions(self) -> None:
        product = {"flow": {}}
        self.assertEqual(gate_module.question_fields(product, {"A": {"process": "cnc"}}), list(QUESTION_FIELDS[:5]))

    def test_question_fields_takes_the_answers_so_far_for_the_print_in_place_rule(self) -> None:
        parts = {"A": {"process": "fdm"}}
        declined = {"processes": {"fdm": {"print_in_place": False}}}
        self.assertNotIn("processes/fdm/pip_clearance_mm/xy", gate_module.question_fields({}, parts, declined))
        self.assertIn("processes/fdm/pip_clearance_mm/xy", gate_module.question_fields({}, parts, {}))

    def test_the_pip_clearance_questions_apply_only_while_print_in_place_is_offered(self) -> None:
        for offer, expected in ((True, [("flow.profile", "flow/profile/asked/processes/fdm/pip_clearance_mm/z")]), (False, [])):
            with self.subTest(print_in_place=offer):
                profile = fx.flow_profile(fdm={"print_in_place": offer})
                del profile["asked"]["processes/fdm/pip_clearance_mm/z"]
                self.assertEqual(self.check("detail", profile), expected)


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

    def parsed(self, key: str, word: str) -> Any:
        """product.yaml at release with `key: <word>` written bare, read by the repo's loader."""
        product = fx.product(stage="release")
        product["flow"]["profile"] = fx.flow_profile()
        product["manufacturing"] = {"processes": ["fdm"]}  # a resolved plan: an unresolved one blocks by itself
        if key == "enabled":
            product["flow"]["profile"]["fea"] = {"enabled": True, "mode": "accurate"} if word == "on" else {"enabled": False}
        text = yaml.safe_dump(product, sort_keys=False)
        lines = [line for line in text.splitlines() if line.lstrip().startswith(f"{key}:") and not line.endswith(": user")]
        self.assertEqual(len(lines), 1, key)
        indent = lines[0][:len(lines[0]) - len(lines[0].lstrip())]
        return yaml_loader.safe_load(text.replace(lines[0], f"{indent}{key}: {word}"))

    def test_a_bare_word_is_the_value_of_a_two_state_field(self) -> None:
        for key, word, path, value in self.BOOLEAN:
            with self.subTest(f"{key}: {word}"):
                product = self.parsed(key, word)
                self.assertEqual(gate_module.value_at(product["flow"]["profile"], path), value)
                self.assertEqual(gate_module.gate(product, {}, approved)[1], [])

    def test_a_bare_on_or_off_in_a_field_with_more_states_blocks_and_asks_for_quotes(self) -> None:
        for key, word, path in self.STRING:
            with self.subTest(f"{key}: {word}"):
                _stage, found = gate_module.gate(self.parsed(key, word), {}, approved)
                self.assertEqual([(item.code, item.path) for item in found], [("flow.profile_unquoted", f"flow/profile/{path}")])
                self.assertIn(f'write "{word}" in quotes', found[0].message)


class Conflicts(unittest.TestCase):
    """What validate.py reports as flow.conflict, one rule per test."""

    def setUp(self) -> None:
        self.documents = fx.tst_record()

    def conflicts(self, fea_cases: tuple[str, ...] = (), **profile: Any) -> list[tuple[str, bool]]:
        product = self.documents["product.yaml"]
        product["flow"]["profile"] = fx.flow_profile(**profile)
        parts = {rel.split("/")[1]: doc for rel, doc in self.documents.items() if rel.startswith("parts/")}
        found = gate_module.conflicts(product, self.documents["requirements.yaml"], self.documents["interfaces.yaml"],
                                      parts, fea_cases)
        return [(item.rule, item.refuse) for item in found]

    def pip_joint(self, **interface: Any) -> None:
        joint = {"joint": "hinge", "print_pose": {"J-001": 0}, "break_free": {"method": "flex by hand"},
                 "gap_features": [[{"PN": "TST-0001", "feature": "knuckle"}, {"PN": "TST-0002", "feature": "pin"}]]}
        self.documents["interfaces.yaml"]["interfaces"][0]["functional"]["print_in_place"] = [joint]
        self.documents["interfaces.yaml"]["interfaces"][0].update(interface)

    def test_the_fixture_profile_conflicts_with_nothing_in_the_fixture_record(self) -> None:
        self.assertEqual(self.conflicts(), [])

    def test_a_product_without_a_profile_conflicts_with_nothing(self) -> None:
        self.assertEqual(gate_module.conflicts({}, {}, {}, {}, ()), [])

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

    def test_a_list_the_record_holds_as_something_else_counts_as_empty_and_never_crashes(self) -> None:
        profile = fx.flow_profile(stack_up="off", fea={"enabled": False})
        product = {**self.documents["product.yaml"], "flow": {"stage": "parts", "recheck_from": "parts", "profile": profile}}
        for name, requirements, interfaces in (("scalars", {"requirements": 7}, {"interfaces": 7}),
                                               ("strings", {"requirements": "x"}, {"interfaces": "x"}),
                                               ("not mappings", [], []), ("items", {"requirements": [3]}, {"interfaces": [3]})):
            with self.subTest(name):
                self.assertEqual(gate_module.conflicts(product, requirements, interfaces, {}, ()), [])


if __name__ == "__main__":
    unittest.main()
