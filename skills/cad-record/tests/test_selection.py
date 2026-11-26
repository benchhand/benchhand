#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""selection.py: the use case gates a final recommendation; hard constraints (mandatory requirements, capability
and material approvals, the optical route) eliminate before preferences rank; the comparison, the proposal and
its findings; and the read-only CLI. Plan section 11 rows 1-5 and the transparency case, on synthetic TST
records from tests/fixtures/selection/scenarios.py."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPT = SKILL / "scripts" / "selection.py"
EXAMPLE = SKILL.parents[1] / "examples" / "lamp-arm" / "hw"


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402


scenarios = load("cad_record_selection_scenarios", HERE / "fixtures" / "selection" / "scenarios.py")
fx = scenarios.fx
mi = fx.intent_module()
selection = load("cad_record_selection", SCRIPT)


FINDING = fx.ref_validator("https://srobroek.com/cad/schemas/manufacturing.schema.json#/$defs/finding")
RECOMMENDATION = fx.ref_validator("https://srobroek.com/cad/schemas/manufacturing-selection.schema.json#/$defs/recommendation")


def write_at(hw: Path, documents: dict[str, Any], *, current_digest: bool = True) -> Path:
    """Write `documents` as a project at `hw`; record the current input digest in selection.yaml unless the test
    wants the placeholder (a stale assessment)."""
    hw = fx.write_record(hw, documents)
    if current_digest:
        path = hw / "manufacturing" / "selection.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["assessment"]["input_sha256"] = selection.input_digest(mi.read_documents(hw)[0])
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return hw


def write(test: unittest.TestCase, documents: dict[str, Any], *, current_digest: bool = True) -> Path:
    temporary = tempfile.TemporaryDirectory(prefix="cad-selection-test-")
    test.addCleanup(temporary.cleanup)
    return write_at(Path(temporary.name) / "project" / "hw", documents, current_digest=current_digest)


def assess(test: unittest.TestCase, documents: dict[str, Any], **options: Any) -> dict[str, Any]:
    report = selection.assess_hw(write(test, documents, **options))
    test.assertEqual(report["problems"], [], "the scenario record itself must be valid")
    return report


_REPORTS: dict[str, dict[str, Any]] = {}
_SCRATCH: list[tempfile.TemporaryDirectory[str]] = []


def setUpModule() -> None:
    _SCRATCH.append(tempfile.TemporaryDirectory(prefix="cad-selection-reports-"))


def tearDownModule() -> None:
    _SCRATCH.pop().cleanup()
    _REPORTS.clear()


def scenario_report(test: unittest.TestCase, build: Any) -> dict[str, Any]:
    """The report of an unmodified scenario, assessed once per module run; tests only read it."""
    name = build.__name__
    if name not in _REPORTS:
        report = selection.assess_hw(write_at(Path(_SCRATCH[0].name) / name / "hw", build()))
        test.assertEqual(report["problems"], [], "the scenario record itself must be valid")
        _REPORTS[name] = report
    return _REPORTS[name]


def only_decision(report: dict[str, Any]) -> dict[str, Any]:
    assert len(report["decisions"]) == 1, report["decisions"]
    return report["decisions"][0]


def candidate(report: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(c for d in report["decisions"] for c in d["candidates"] if c["id"] == ident)


def evaluations(entry: dict[str, Any], constraint: str) -> list[dict[str, Any]]:
    return [e for e in entry["evaluations"] if e["constraint"] == constraint]


def findings(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["code"] == code]


def tree_digest(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def profile(documents: dict[str, Any], kind: str, ident: str) -> dict[str, Any]:
    return next(p for p in documents[f"manufacturing/{kind}.yaml"]["profiles"] if p["id"] == ident)


def selection_candidate(documents: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(c for c in documents["manufacturing/selection.yaml"]["candidates"] if c["id"] == ident)


class AssessmentBeforeFinalRecommendation(unittest.TestCase):
    """Row 1: missing material-to-choice and safety facts refuse a final recommendation, never a report."""

    def test_an_unassessed_use_case_allows_only_a_conditional_report(self) -> None:
        report = scenario_report(self, scenarios.unassessed_case)
        decision = only_decision(report)
        self.assertEqual((report["status"], selection.exit_status(report)), ("conditional", 1))
        self.assertEqual(candidate(report, "convenient_print")["status"], "feasible", "the print itself meets REQ-001")
        self.assertFalse(decision["final_allowed"])
        recommendation = decision["recommendation"]
        self.assertEqual((recommendation["status"], recommendation["primary"]), ("conditional", "convenient_print"))
        self.assertNotIn("end_use", recommendation, "no end-use route without the use case it serves")
        refused = " | ".join(decision["refused_because"])
        self.assertIn("the use case is not confirmed by a decision", refused)
        self.assertIn("the service class is unknown", refused)
        self.assertIn("Q-001", refused)
        self.assertTrue(set(decision["refused_because"]) <= set(recommendation["limits"]), "the limits state why it is conditional")
        missing = {m["field"]: m for m in report["use_case"]["missing"]}
        self.assertEqual((missing["use_case.service_class"]["question"], missing["use_case.service_class"]["safety_relevant"]),
                         ("Q-001", True))
        self.assertTrue(missing["use_case.environment.temperature"]["could_change_choice_or_safety"])
        self.assertEqual([q["id"] for q in report["use_case"]["open_questions"]][:2], ["Q-001", "Q-002"])
        (finding,) = findings(report, "selection.use_case_incomplete")
        self.assertEqual((finding["severity"], finding["outcome"]), ("major", "unknown"))
        self.assertIn("Q-001", finding["remedy"])
        computed = evaluations(candidate(report, "convenient_print"), "REQ-001")
        self.assertTrue(all("the operating temperature is unknown" in e["uncertainty"] for e in computed))

    def test_an_unknown_load_magnitude_refuses_a_final_choice_and_is_named(self) -> None:
        documents = scenarios.qualified_polymer_cover()
        documents["product.yaml"]["use_case"]["loads"][0]["magnitude"] = fx.unknown("never measured")
        report = assess(self, documents)
        self.assertEqual((report["status"], only_decision(report)["final_allowed"]), ("conditional", False))
        self.assertIn("use_case.loads.service_load.magnitude", [m["field"] for m in report["use_case"]["missing"]])

    def test_the_report_is_read_only(self) -> None:
        hw = write(self, scenarios.unassessed_case())
        before = tree_digest(hw.parent)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(hw.parent)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Missing facts that could change the material or process choice", result.stdout)
        self.assertIn("### Recommendation (conditional)", result.stdout)
        self.assertEqual(tree_digest(hw.parent), before, "selection.py never writes the design record")


class NotAssessedIsNoPass(unittest.TestCase):
    """D37: a mandatory evaluation that is not_assessed blocks nothing, and the suitability text never counts it as a pass.
    The seam is an Evaluation handed to the suitability text: nothing computes a not_assessed evaluation yet."""

    @staticmethod
    def suitability(*outcomes: tuple[str, str]) -> str:
        evaluations = [selection.Evaluation(name, True, outcome, {"PN": "TST-0001"}, "m") for name, outcome in outcomes]
        assessed = selection._Assessed(None, evaluations, status=selection.FEASIBLE)
        return selection._Assessor.suitability(None, assessed)

    def test_a_not_assessed_constraint_is_named_apart_from_the_passes(self) -> None:
        text = self.suitability(("REQ-001", "pass"), ("REQ-002", "not_assessed"))
        self.assertIn("(REQ-001)", text)
        self.assertIn("not assessed, not a pass: REQ-002", text)

    def test_the_recommendation_reason_names_them_too(self) -> None:
        from types import SimpleNamespace

        evaluations = [selection.Evaluation(n, True, o, {"PN": "TST-0001"}, "m") for n, o in
                       (("REQ-001", "pass"), ("REQ-002", "not_assessed"))]
        primary = selection._Assessed(SimpleNamespace(raw={}, parts=(), id="C1"), evaluations, status=selection.FEASIBLE)
        requirements = {name: SimpleNamespace(evidence_required=()) for name in ("REQ-001", "REQ-002")}
        assessor = SimpleNamespace(record=SimpleNamespace(requirements=requirements))
        proposal = selection._recommendation(assessor, [primary], primary, None, None, False, [], {"optical": [], "prototype": None, "change_triggers": []})
        text = json.dumps(proposal)
        self.assertIn("meets every mandatory constraint it was assessed against: REQ-001; not assessed, not a pass: REQ-002", text)

    def test_a_candidate_with_nothing_not_assessed_reads_as_before(self) -> None:
        self.assertEqual(self.suitability(("REQ-001", "pass")),
                         "feasible: every mandatory constraint assessed passes (REQ-001)")


class PrintableButUnsuitable(unittest.TestCase):
    """Row 2: a part that prints (envelope, calibrated printer, approved material) still fails its service."""

    def test_a_sourced_heat_criterion_eliminates_the_printable_route(self) -> None:
        report = scenario_report(self, scenarios.hot_loaded_bracket)
        printed = candidate(report, "printed_bracket")
        self.assertEqual((printed["status"], printed["eliminated_by"], printed["rank"]), ("eliminated", ["REQ-002"], None))
        for constraint in ("material_form", "capability", "material_approval", "REQ-001"):
            self.assertTrue(all(e["outcome"] == "pass" for e in evaluations(printed, constraint)), constraint)
        (heat,) = evaluations(printed, "REQ-002")
        self.assertEqual((heat["outcome"], heat["measured"], heat["basis"]), ("fail", {"value": 65.0, "units": "deg_C"}, "supplier_typical"))
        self.assertEqual(heat["sources"][0]["kind"], "datasheet")
        recommendation = only_decision(report)["recommendation"]
        self.assertEqual((recommendation["status"], recommendation["primary"], recommendation["end_use"]),
                         ("final", "machined_bracket", "machined_bracket"))
        self.assertEqual(recommendation["governing_requirements"], ["REQ-002"])
        self.assertTrue(any("printed_bracket is eliminated: REQ-002" in reason for reason in recommendation["reasons"]))
        (finding,) = findings(report, "selection.recorded_route_eliminated")
        self.assertEqual((finding["severity"], finding["subject"]["PN"], finding["requirements"]), ("major", "TST-0201", ["REQ-002"]))
        self.assertEqual(finding["criterion"]["property"], "max_service_temperature")
        self.assertIn("unsuitable for its intended service", finding["consequence"])
        self.assertEqual((report["status"], selection.exit_status(report)), ("final_ready", 0))

    def test_the_eliminated_print_may_only_be_a_limited_prototype(self) -> None:
        report = scenario_report(self, scenarios.hot_loaded_bracket)
        decision = only_decision(report)
        self.assertEqual(decision["recommendation"]["prototype"], "printed_bracket")
        self.assertNotEqual(decision["recommendation"].get("fallback"), "printed_bracket")
        prototype = decision["prototype"]
        self.assertIn("never be the primary, end-use or fallback", prototype["role"])
        cannot = " | ".join(prototype["cannot_validate"])
        self.assertIn("REQ-002: fail on the prototype route", cannot)
        self.assertIn("strength, stiffness, fatigue, creep, heat, wear, service life", cannot)
        self.assertTrue(any("geometry, fit, assembly order" in text for text in prototype["can_validate"]))


class PolymerAcceptedWithEvidence(unittest.TestCase):
    """Row 3: a polymer that demonstrably meets its criteria is recommended; functional is not a reason for metal."""

    def test_a_qualified_polymer_is_the_final_primary(self) -> None:
        report = scenario_report(self, scenarios.qualified_polymer_cover)
        decision = only_decision(report)
        self.assertEqual([(c["id"], c["status"], c["rank"]) for c in decision["candidates"]],
                         [("printed_cover", "feasible", 1), ("machined_cover", "feasible", 2)])
        recommendation = decision["recommendation"]
        self.assertEqual((recommendation["status"], recommendation["primary"], recommendation["fallback"]),
                         ("final", "printed_cover", "machined_cover"))
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["recorded"]["assessment"]["current"])
        self.assertEqual((report["status"], selection.exit_status(report)), ("final_ready", 0))
        accuracy = decision["comparison"][0]["accuracy_finish_stability"]
        self.assertIn("hole_diameter_deviation -0.15..0.05 mm", accuracy)
        self.assertIn("no calibration for tst_metal_d_bar", decision["comparison"][1]["accuracy_finish_stability"])

    def test_a_room_temperature_value_does_not_pass_at_the_operating_temperature(self) -> None:
        documents = scenarios.qualified_polymer_cover()
        polymer = profile(documents, "materials", "tst_polymer_c_printed")
        polymer["properties"][:2] = scenarios.printed("yield_strength", 40, "MPa", temperature=(23, 23))
        report = assess(self, documents)
        printed = candidate(report, "printed_cover")
        self.assertEqual((printed["status"], printed["unknown"]), ("conditional", ["REQ-001"]))
        (strength,) = evaluations(printed, "REQ-001")
        self.assertIn("15..35 deg_C requested", " ".join(strength["reasons"]))
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "machined_cover",
                         "a feasible candidate outranks a conditional one whatever the scores")

    def with_unknown_capability(self, ident: str) -> dict[str, Any]:
        """The cover scenario with `ident`'s route step naming no machine, as a schema-valid unknown fact."""
        documents = scenarios.qualified_polymer_cover()
        selection_candidate(documents, ident)["parts"][0]["route"][0]["capability"] = fx.unknown("no machine named")
        return documents

    def assessed(self, documents: dict[str, Any], flag: str | None) -> dict[str, Any]:
        """The assessment of the record on disk under CAD_CALIBRATION=`flag` (None: unset), the CLI's path."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CAD_CALIBRATION", None)
            if flag is not None:
                os.environ["CAD_CALIBRATION"] = flag
            return assess(self, documents)

    def test_a_non_fdm_step_with_an_unknown_capability_is_conditional_under_either_flag(self) -> None:
        for flag in (None, "1"):
            with self.subTest(flag=flag):
                report = self.assessed(self.with_unknown_capability("machined_cover"), flag)
                machined = candidate(report, "machined_cover")
                self.assertEqual((machined["status"], machined["unknown"]), ("conditional", ["capability"]))
                (capability,) = evaluations(machined, "capability")
                self.assertIn("no capability profile", capability["reasons"][0])
                self.assertEqual(only_decision(report)["recommendation"]["primary"], "printed_cover")
                self.assertIn("machined_cover", selection.render_markdown(report))

    def test_an_fdm_step_with_an_unknown_capability_is_a_note_with_the_flag_off_and_conditional_with_it_on(self) -> None:
        """No machine recorded is no fault while calibration is off: the guideline values govern, the capability is
        not_assessed (never a pass, never blocking). With the flag on a calibration needs the machine, so it stays unknown."""
        off = self.assessed(self.with_unknown_capability("printed_cover"), None)
        printed = candidate(off, "printed_cover")
        self.assertEqual((printed["status"], printed["unknown"]), ("feasible", []))
        (capability,) = evaluations(printed, "capability")
        self.assertEqual(capability["outcome"], "not_assessed")
        reasons = " ".join(capability["reasons"])
        self.assertIn("machine not recorded", reasons)
        self.assertIn("guideline", reasons)
        decision = only_decision(off)
        self.assertIn("not assessed, not a pass: capability", json.dumps(decision))
        self.assertEqual((decision["recommendation"]["status"], decision["recommendation"]["primary"]), ("final", "printed_cover"))
        self.assertEqual(selection.exit_status(off), 0)
        on = self.assessed(self.with_unknown_capability("printed_cover"), "1")
        flagged = candidate(on, "printed_cover")
        self.assertEqual((flagged["status"], flagged["unknown"]), ("conditional", ["capability"]))
        self.assertEqual(only_decision(on)["recommendation"]["primary"], "machined_cover")

    def test_the_machine_is_required_for_an_outsourced_or_a_safety_critical_fdm_route(self) -> None:
        """The no-machine exception covers only a route whose equipment evidence is optional: a supplier's capability and a
        safety-critical service need a capability profile whatever the flag says. A step that states no provider is
        outsourced when the part is recorded as bought to its drawing. An unknown service class could be safety_critical."""
        outsourced = self.with_unknown_capability("printed_cover")
        selection_candidate(outsourced, "printed_cover")["parts"][0]["route"][0]["provider"] = "outsourced"
        critical = self.with_unknown_capability("printed_cover")
        critical["product.yaml"]["use_case"]["service_class"] = fx.known("safety_critical")
        unassessed = self.with_unknown_capability("printed_cover")
        unassessed["product.yaml"]["use_case"]["service_class"] = fx.unknown("service class not assessed")
        bought = self.with_unknown_capability("printed_cover")
        del selection_candidate(bought, "printed_cover")["parts"][0]["route"][0]["provider"]
        bought["parts/TST-0301/part.yaml"].update(make_buy="buy", procurement={"mode": "made_to_drawing"},
                                                  supplier="TST print bureau")
        for name, documents in (("outsourced", outsourced), ("safety_critical", critical), ("made_to_drawing", bought),
                                ("unknown service class", unassessed)):
            with self.subTest(name):
                report = self.assessed(documents, None)
                printed = candidate(report, "printed_cover")
                self.assertIn("capability", printed["unknown"])
                (capability,) = evaluations(printed, "capability")
                self.assertEqual(capability["outcome"], "unknown")

    def test_the_effective_calibration_flag_is_part_of_the_confirmation_digest(self) -> None:
        """The flag can change the winning candidate, so an assessment recorded under one setting is stale under the other."""
        documents = scenarios.qualified_polymer_cover()
        self.assertNotEqual(selection.input_digest(documents, calibration=False), selection.input_digest(documents, calibration=True))
        hw = write(self, self.with_unknown_capability("printed_cover"))  # records the digest under the flag unset
        for flag, current in ((None, True), ("1", False)):
            with self.subTest(flag=flag), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CAD_CALIBRATION", None)
                if flag is not None:
                    os.environ["CAD_CALIBRATION"] = flag
                self.assertIs(selection.assess_hw(hw)["recorded"]["assessment"]["current"], current)

    def test_family_guidance_never_passes_a_requirement(self) -> None:
        documents = scenarios.qualified_polymer_cover()
        polymer = profile(documents, "materials", "tst_polymer_c_printed")
        polymer.update(level="family", grade=fx.not_applicable("family guidance only"))
        report = assess(self, documents)
        reasons = " ".join(r for e in evaluations(candidate(report, "printed_cover"), "REQ-001") for r in e["reasons"])
        self.assertEqual(candidate(report, "printed_cover")["status"], "conditional")
        self.assertIn("family guidance", reasons)

    def test_a_recorded_assessment_goes_stale_when_its_inputs_change(self) -> None:
        documents = scenarios.qualified_polymer_cover()
        hw = write(self, documents)
        requirements = hw / "requirements.yaml"
        data = yaml.safe_load(requirements.read_text(encoding="utf-8"))
        data["requirements"][1]["acceptance"][0]["value"] = 55
        requirements.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        report = selection.assess_hw(hw)
        self.assertFalse(report["recorded"]["assessment"]["current"])
        (finding,) = findings(report, "selection.assessment_stale")
        self.assertEqual(finding["severity"], "major")
        self.assertEqual(finding["criterion"]["source"]["origin"],
                         "benchhand manufacturing-intent policy (the user's specification)")


class JustifiedAlternatives(unittest.TestCase):
    """Row 4: metal, purchased, sheet and hybrid alternatives win when the print cannot meet the service."""

    def test_machined_purchased_and_hybrid_alternatives_replace_a_printed_shaft(self) -> None:
        report = scenario_report(self, scenarios.wear_shaft_options)
        decision = only_decision(report)
        self.assertEqual([(c["id"], c["status"], c["rank"]) for c in decision["candidates"]],
                         [("purchased_shaft", "feasible", 1), ("hybrid_pin", "feasible", 2), ("machined_shaft", "feasible", 3),
                          ("printed_shaft", "eliminated", None)])
        self.assertEqual(candidate(report, "printed_shaft")["eliminated_by"], ["REQ-001", "REQ-002"])
        self.assertEqual(candidate(report, "machined_shaft")["families"], ["additive", "hybrid", "machined"])
        self.assertIn("purchased", candidate(report, "hybrid_pin")["families"])
        recommendation = decision["recommendation"]
        self.assertEqual({key: recommendation.get(key) for key in ("primary", "end_use", "fallback", "prototype")},
                         {"primary": "purchased_shaft", "end_use": "purchased_shaft", "fallback": "hybrid_pin",
                          "prototype": "printed_shaft"})
        self.assertEqual(recommendation["governing_requirements"], ["REQ-001", "REQ-002"])
        kinds = {trigger["kind"] for trigger in decision["change_triggers"]}
        self.assertLessEqual({"loads", "temperature", "life", "tolerances", "equipment", "volume"}, kinds)
        self.assertTrue(any("radial_load above 50 N" in text for text in recommendation["what_would_change"]))
        self.assertTrue(any("TST-0401" in change and "make_buy becomes buy" in change for change in recommendation["proposed_changes"]))
        self.assertTrue(any(text.startswith("cannot validate: REQ-001") for text in recommendation["prototype_validates"]))
        pairs = candidate(report, "machined_shaft")["pairs"]
        self.assertEqual([(p["interface"], p["materials"], p["critical"]) for p in pairs],
                         [("IF-001", ["tst_metal_o_bar", "tst_polymer_m_printed"], True)])
        self.assertIn("IF-001 wear and friction evidence", decision["comparison"][2]["evidence"])
        wear = [f for f in findings(report, "selection.recorded_route_eliminated") if f["requirements"] == ["REQ-002"]]
        self.assertEqual(wear[0]["subject"]["interface"], "IF-001")

    def test_a_result_for_another_state_or_condition_does_not_meet_the_requirement(self) -> None:
        """REQ-002 asks for the assembled wear depth after the run-in. A pass measured in another dimension state or
        under other conditions is a different criterion: a mismatch, never credited."""
        required = {"state": "assembled", "conditions": "TST after 1000 h run-in at 40 deg_C"}
        stated = {"another state": {**required, "state": "as_manufactured"}, "no state": {"conditions": required["conditions"]},
                  "other conditions": {**required, "conditions": "TST new, at 23 deg_C"}}
        for case, extra in stated.items():
            with self.subTest(case):
                documents = scenarios.wear_shaft_options()
                documents["requirements.yaml"]["requirements"][1]["acceptance"][0].update(required)
                (written,) = selection_candidate(documents, "purchased_shaft")["evaluations"]
                written["criterion"].update(extra)
                report = assess(self, documents)
                purchased = candidate(report, "purchased_shaft")
                self.assertEqual((purchased["status"], purchased["unknown"]), ("conditional", ["REQ-002"]))
                self.assertIn("evaluation_mismatch_purchased_shaft_req_002",
                              [f["id"] for f in findings(report, "selection.evaluation_mismatch")])

    def test_a_missing_evaluation_keeps_an_alternative_conditional(self) -> None:
        documents = scenarios.wear_shaft_options()
        selection_candidate(documents, "purchased_shaft")["evaluations"] = []
        report = assess(self, documents)
        purchased = candidate(report, "purchased_shaft")
        self.assertEqual((purchased["status"], purchased["unknown"]), ("conditional", ["REQ-002"]))
        (unknown,) = evaluations(purchased, "REQ-002")
        self.assertIn("no evaluation states wear_depth <= 0.1 mm", unknown["reasons"][0])
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "hybrid_pin")

    def test_a_sheet_frame_replaces_a_printed_frame_that_is_not_stiff_enough(self) -> None:
        report = scenario_report(self, scenarios.plate_frame_options)
        decision = only_decision(report)
        self.assertEqual(candidate(report, "printed_frame")["eliminated_by"], ["REQ-001"])
        self.assertEqual((decision["recommendation"]["primary"], decision["recommendation"]["fallback"]),
                         ("sheet_frame", "machined_frame"))
        sheet = candidate(report, "sheet_frame")
        self.assertEqual(sheet["families"], ["sheet"])
        self.assertEqual([e["outcome"] for e in evaluations(sheet, "material_approval")], ["pass", "pass"])

    def test_a_laser_approval_without_extraction_is_not_assumed(self) -> None:
        documents = scenarios.plate_frame_options()
        profile(documents, "capabilities", "tst_laser")["materials"][0].pop("extraction")
        report = assess(self, documents)
        sheet = candidate(report, "sheet_frame")
        self.assertEqual((sheet["status"], sheet["unknown"]), ("conditional", ["material_approval"]))
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "machined_frame")

    def test_an_approval_outside_the_step_scope_is_not_credited(self) -> None:
        """tst_laser's approval of the sheet is scoped by operation, thickness and coating. An approval for engraving only,
        one that states no operation, or one whose thickness or coating scope is unresolved or is for other stock, never
        approves the cutting step."""
        def approval(documents: dict[str, Any]) -> dict[str, Any]:
            return profile(documents, "capabilities", "tst_laser")["materials"][0]

        def cut(documents: dict[str, Any]) -> dict[str, Any]:
            return selection_candidate(documents, "sheet_frame")["parts"][0]["route"][0]

        cases = {
            "engraving only": lambda d: approval(d).update(operations=["engrave"]),
            "operation scope not stated": lambda d: approval(d).pop("operations"),
            "approved thickness not stated": lambda d: approval(d).pop("thickness"),
            "coating not identified": lambda d: approval(d).pop("coating", None),
            "stock thickness not stated": lambda d: cut(d).pop("stock", None),
            "stock coating not stated": lambda d: cut(d)["stock"].pop("coating"),
            "approved for another coating": lambda d: cut(d)["stock"].update(coating=fx.known("TST protective film")),
        }
        for case, change in cases.items():
            with self.subTest(case):
                documents = scenarios.plate_frame_options()
                change(documents)
                report = assess(self, documents)
                sheet = candidate(report, "sheet_frame")
                self.assertEqual((sheet["status"], sheet["unknown"]), ("conditional", ["material_approval"]))
                self.assertEqual([e["outcome"] for e in evaluations(sheet, "material_approval")], ["unknown", "pass"])
                self.assertEqual(only_decision(report)["recommendation"]["primary"], "machined_frame")

    def test_an_unstated_thickness_or_coating_names_how_to_record_it(self) -> None:
        """The remedy of an unresolved scope says what to record, including "none" for uncoated stock."""
        def approval(documents: dict[str, Any]) -> dict[str, Any]:
            return profile(documents, "capabilities", "tst_laser")["materials"][0]

        def stock(documents: dict[str, Any]) -> dict[str, Any]:
            return selection_candidate(documents, "sheet_frame")["parts"][0]["route"][0]["stock"]

        cases = {
            "approved thickness": (lambda d: approval(d).pop("thickness"), "thickness range"),
            "approved coating": (lambda d: approval(d).pop("coating"), 'coating it is approved for, or "none"'),
            "stock coating": (lambda d: stock(d).pop("coating"), 'stock.coating of the step, or "none" for uncoated stock'),
            "stock thickness": (lambda d: stock(d).pop("thickness"), "stock.thickness of the step"),
        }
        for case, (change, wanted) in cases.items():
            with self.subTest(case):
                documents = scenarios.plate_frame_options()
                change(documents)
                report = assess(self, documents)
                (cut, _) = evaluations(candidate(report, "sheet_frame"), "material_approval")
                self.assertEqual(cut["outcome"], "unknown")
                self.assertIn(wanted, cut["remedy"])

    def test_a_stock_thickness_outside_the_approved_range_is_not_credited(self) -> None:
        """tst_laser approves the sheet at 3 mm. A 5 mm stock is stated, so the approval does not cover the cut."""
        documents = scenarios.plate_frame_options()
        selection_candidate(documents, "sheet_frame")["parts"][0]["route"][0]["stock"]["thickness"] = fx.known(fx.quantity(5))
        report = assess(self, documents)
        sheet = candidate(report, "sheet_frame")
        self.assertEqual((sheet["status"], sheet["unknown"]), ("conditional", ["material_approval"]))
        (cut, _) = evaluations(sheet, "material_approval")
        self.assertEqual(cut["outcome"], "unknown")
        self.assertTrue(any("lies outside" in reason for reason in cut["reasons"]), cut["reasons"])

    def test_no_feasible_candidate_names_the_route_families_not_yet_considered(self) -> None:
        documents = scenarios.plate_frame_options()
        documents["manufacturing/selection.yaml"]["candidates"] = documents["manufacturing/selection.yaml"]["candidates"][:1]
        report = assess(self, documents)
        decision = only_decision(report)
        self.assertIsNone(decision["recommendation"])
        self.assertEqual((report["status"], selection.exit_status(report)), ("blocked", 1))
        (finding,) = findings(report, "selection.no_feasible_candidate")
        alternatives = " | ".join(a["text"] for a in finding["alternatives"])
        for family in ("machined", "purchased", "sheet", "hybrid"):
            self.assertIn(family, alternatives)
        self.assertIn("never relax a mandatory requirement", finding["remedy"])


class ExistingEquipmentIsNotSelected(unittest.TestCase):
    """Pre-existing equipment the product mounts onto is outside the product: never a candidate part, never a make_buy proposal."""

    def test_a_candidate_naming_existing_equipment_proposes_nothing_for_it(self) -> None:
        documents = scenarios.wear_shaft_options()
        documents["parts/TST-0409/part.yaml"] = fx.existing_part("TST-0409", "Mount face")
        selection_candidate(documents, "purchased_shaft")["parts"].append(
            scenarios.cpart("TST-0409", "tst_polymer_m_printed", scenarios.step("fdm", "tst_printer", "local")))
        report = assess(self, documents)
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "purchased_shaft")
        self.assertNotIn("TST-0409", json.dumps(report["decisions"]))

    def test_a_candidate_of_only_existing_equipment_is_never_recommended(self) -> None:
        documents = scenarios.wear_shaft_options()
        documents["parts/TST-0409/part.yaml"] = fx.existing_part("TST-0409", "Mount face")
        documents["manufacturing/selection.yaml"]["candidates"].append(scenarios.candidate(
            "mount_only", "The mount face alone", "existing equipment only",
            [scenarios.cpart("TST-0409", "tst_polymer_m_printed", scenarios.step("fdm", "tst_printer", "local"))],
            cost=1, scores={"PRF-001": 10, "PRF-002": 10}))
        report = assess(self, documents)
        self.assertNotIn("mount_only", json.dumps(report["decisions"]))

    def test_a_recorded_final_choice_of_an_existing_only_candidate_blocks(self) -> None:
        documents = scenarios.wear_shaft_options()
        documents["parts/TST-0409/part.yaml"] = fx.existing_part("TST-0409", "Mount face")
        selection_doc = documents["manufacturing/selection.yaml"]
        selection_doc["candidates"].append(scenarios.candidate(
            "mount_only", "The mount face alone", "existing equipment only",
            [scenarios.cpart("TST-0409", "tst_polymer_m_printed", scenarios.step("fdm", "tst_printer", "local"))], cost=1))
        selection_doc["status"] = "confirmed"
        selection_doc["assessment"]["decision"] = "DR-001"
        selection_doc["recommendation"] = {"status": "final", "primary": "mount_only", "reasons": ["TST: cheapest"],
                                           "limits": ["TST: none recorded"], "what_would_change": ["TST: nothing recorded"]}
        report = assess(self, documents)
        self.assertEqual([f["severity"] for f in findings(report, "selection.recorded_recommendation_dropped")], ["critical"])
        self.assertEqual((report["status"], selection.exit_status(report)), ("blocked", 1))

    def test_a_non_string_recommendation_role_with_no_candidates_is_not_assessed(self) -> None:
        """The schema rejects a list role; a record read without it (as evidence assessment reads one) still reports."""
        documents = scenarios.wear_shaft_options()
        documents["manufacturing/selection.yaml"]["candidates"] = []
        documents["manufacturing/selection.yaml"]["recommendation"] = {
            "status": "conditional", "primary": ["mount_only"], "reasons": ["TST: x"], "limits": ["TST: y"],
            "what_would_change": ["TST: z"]}
        report = selection.assess(mi.normalize_record(documents))
        self.assertEqual((report["status"], [f["code"] for f in report["findings"]]),
                         ("not_assessed", ["selection.not_assessed"]))

    def test_a_stale_profile_on_an_existing_equipment_row_is_ignored(self) -> None:
        documents = scenarios.wear_shaft_options()
        documents["parts/TST-0409/part.yaml"] = fx.existing_part("TST-0409", "Mount face")
        selection_candidate(documents, "purchased_shaft")["parts"].append(
            scenarios.cpart("TST-0409", "tst_retired_profile", scenarios.step("fdm", "tst_retired_printer", "local")))
        report = assess(self, documents)
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "purchased_shaft")


class MandatoryConstraintsBeforeScores(unittest.TestCase):
    """Row 5: hard constraints eliminate before any score ranks; an unknown mandatory result never wins."""

    def test_a_score_never_compensates_a_mandatory_failure(self) -> None:
        report = scenario_report(self, scenarios.mandatory_vs_score)
        decision = only_decision(report)
        self.assertEqual([(c["id"], c["status"], c["rank"], c["preference"]["score"]) for c in decision["candidates"]],
                         [("compliant", "feasible", 1, 10.0), ("unproven", "conditional", 2, 1000.0),
                          ("cheap_unsafe", "eliminated", None, None)])
        cheap = candidate(report, "cheap_unsafe")
        computed = [e for e in evaluations(cheap, "REQ-001") if not e["authored"]]
        self.assertTrue(computed and all(e["outcome"] == "fail" for e in computed))
        self.assertIn("even the typical value fails", " ".join(computed[0]["reasons"]))
        self.assertFalse(cheap["prototype_eligible"], "a critical-risk failure rules it out even as a prototype")
        recommendation = decision["recommendation"]
        self.assertEqual((recommendation["primary"], recommendation["fallback"]), ("compliant", "unproven"))
        self.assertNotIn("prototype", recommendation)
        self.assertIn("not ranked", decision["comparison"][2]["preference"])

    def test_typical_values_cannot_pass_production_service(self) -> None:
        report = scenario_report(self, scenarios.mandatory_vs_score)
        unproven = candidate(report, "unproven")
        self.assertEqual(unproven["unknown"], ["REQ-001"])
        reasons = " ".join(r for e in evaluations(unproven, "REQ-001") for r in e["reasons"])
        self.assertIn("needs a justified design allowable", reasons)
        compliant = evaluations(candidate(report, "compliant"), "REQ-001")
        self.assertEqual([(e["outcome"], e["basis"]) for e in compliant], [("pass", "allowable")])

    def test_an_authored_pass_does_not_stand_in_for_a_missing_design_allowable(self) -> None:
        """Production service needs a justified design allowable for a stress criterion. An authored pass of the same
        criterion carries no allowable basis, so the computed unknown stays and the candidate stays conditional."""
        documents = scenarios.mandatory_vs_score()
        selection_candidate(documents, "unproven")["evaluations"] = [
            scenarios.evaluation("REQ-001", "pass", "yield_strength", ">=", 100, "MPa", method="TST coupon test")]
        report = assess(self, documents)
        unproven = candidate(report, "unproven")
        self.assertEqual((unproven["status"], unproven["unknown"]), ("conditional", ["REQ-001"]))
        computed = [e for e in evaluations(unproven, "REQ-001") if not e["authored"]]
        self.assertEqual([e["outcome"] for e in computed], ["unknown"])
        self.assertIn("needs a justified design allowable", " ".join(computed[0]["reasons"]))

    def test_authored_passes_do_not_override_computed_failures_or_weaker_criteria(self) -> None:
        report = scenario_report(self, scenarios.mandatory_vs_score)
        (conflict,) = findings(report, "selection.evaluation_conflict")
        self.assertEqual((conflict["severity"], conflict["requirements"]), ("major", ["REQ-001"]))
        (mismatch,) = findings(report, "selection.evaluation_mismatch")
        self.assertEqual(mismatch["criterion"]["value"], 100)
        self.assertIn("yield_strength >= 30 MPa", mismatch["measured"]["text"])
        self.assertEqual(candidate(report, "unproven")["status"], "conditional")

    def test_a_recorded_final_choice_of_an_eliminated_candidate_blocks(self) -> None:
        report = scenario_report(self, scenarios.mandatory_vs_score)
        self.assertEqual({f["severity"] for f in findings(report, "selection.recorded_recommendation_eliminated")}, {"critical"})
        self.assertEqual(len(findings(report, "selection.final_unsupported")), 1)
        self.assertEqual(findings(report, "selection.recorded_route_eliminated")[0]["severity"], "critical")
        self.assertEqual((report["status"], selection.exit_status(report)), ("blocked", 1))

    def test_a_conditional_candidate_is_never_a_final_primary(self) -> None:
        documents = scenarios.mandatory_vs_score()
        candidates = documents["manufacturing/selection.yaml"]["candidates"]
        documents["manufacturing/selection.yaml"]["candidates"] = [c for c in candidates if c["id"] != "compliant"]
        report = assess(self, documents)
        decision = only_decision(report)
        self.assertEqual((decision["recommendation"]["primary"], decision["recommendation"]["status"]), ("unproven", "conditional"))
        self.assertFalse(decision["final_allowed"])
        self.assertTrue(any("conditional" in reason for reason in decision["refused_because"]))
        self.assertEqual(len(findings(report, "selection.primary_unknown")), 1)


class TransparencyIsAHardConstraint(unittest.TestCase):
    """Decision 2: an optical part is a purchased transparent part or identified transparent stock, never a print."""

    def test_opaque_processes_are_eliminated_and_identified_stock_is_recommended(self) -> None:
        report = scenario_report(self, scenarios.transparent_cover)
        decision = only_decision(report)
        self.assertEqual({c["id"]: c["status"] for c in decision["candidates"]},
                         {"laser_window": "feasible", "purchased_window": "conditional", "fdm_window": "eliminated",
                          "resin_window": "eliminated"})
        resin = candidate(report, "resin_window")
        self.assertEqual(resin["eliminated_by"], ["optical_route"])
        self.assertEqual([e["outcome"] for e in evaluations(resin, "REQ-001")], ["pass"],
                         "a passing transmission figure does not make an opaque process a transparent route")
        self.assertFalse(candidate(report, "fdm_window")["prototype_eligible"])
        recommendation = decision["recommendation"]
        self.assertEqual((recommendation["primary"], recommendation["fallback"]), ("laser_window", "purchased_window"))
        self.assertNotIn("prototype", recommendation, "see-through FDM is not offered even as a prototype")
        (optical,) = decision["optical"]
        criteria = {c["name"]: c for c in optical["purchase_criteria"]}
        self.assertEqual(list(criteria), ["material_identity_grade", "optical_grade", "thickness", "supplier_evidence"])
        self.assertEqual(criteria["optical_grade"]["value"], "light_transmission 92 percent (supplier_typical)")
        self.assertEqual(criteria["supplier_evidence"]["sources"][0]["kind"], "datasheet")
        self.assertEqual(criteria["thickness"]["status"], "unknown")
        self.assertTrue(any("thickness" in change for change in recommendation["proposed_changes"]))
        critical = [f for f in findings(report, "selection.recorded_route_eliminated") if f["severity"] == "critical"]
        self.assertEqual(critical[0]["criterion"]["property"], "optical_route")
        self.assertIn("purchase criteria", critical[0]["remedy"])
        purchased = evaluations(candidate(report, "purchased_window"), "optical_route")[0]
        self.assertIn("not recorded", purchased["reasons"][0])

    def test_recorded_purchase_criteria_make_a_purchased_window_feasible(self) -> None:
        documents = scenarios.transparent_cover()
        part = documents["parts/TST-0601/part.yaml"]
        part.update(make_buy="buy", procurement={"mode": "catalog"}, process="purchased")
        part["intent"]["route"] = [fx.operation("buy", "purchased", purchase={
            "identity": fx.known("TST window 1"), "specification": fx.known("TST clear grade"),
            "ratings": fx.not_applicable("no rating applies"), "optical": fx.known("TST optical grade A"),
            "thickness": fx.known(fx.quantity(3)), "supplier_evidence": [fx.source("supplier", "TST window datasheet")]})]
        report = assess(self, documents)
        purchased = candidate(report, "purchased_window")
        self.assertEqual(purchased["status"], "feasible")
        self.assertEqual([e["outcome"] for e in evaluations(purchased, "optical_route")], ["pass"])


class ComparisonOutput(unittest.TestCase):
    def test_findings_and_recommendations_follow_the_record_schemas(self) -> None:
        for name in ("unassessed_case", "hot_loaded_bracket", "qualified_polymer_cover", "wear_shaft_options",
                     "plate_frame_options", "mandatory_vs_score", "transparent_cover"):
            with self.subTest(scenario=name):
                report = scenario_report(self, getattr(scenarios, name))
                for finding in report["findings"]:
                    self.assertEqual([e.message for e in FINDING.iter_errors(finding)], [], finding["id"])
                ids = [finding["id"] for finding in report["findings"]]
                self.assertEqual(len(ids), len(set(ids)))
                for decision in report["decisions"]:
                    if decision["recommendation"] is not None:
                        self.assertEqual([e.message for e in RECOMMENDATION.iter_errors(decision["recommendation"])], [])

    def test_the_report_is_deterministic_and_renders_every_column(self) -> None:
        hw = write(self, scenarios.wear_shaft_options())
        first, second = selection.assess_hw(hw), selection.assess_hw(hw)
        self.assertEqual(json.dumps(first, sort_keys=False), json.dumps(second, sort_keys=False))
        decision = only_decision(first)
        for row in decision["comparison"]:
            self.assertEqual(list(row), [key for key, _ in selection.COLUMNS])
        markdown = selection.render_markdown(first)
        header = "| " + " | ".join(title for _, title in selection.COLUMNS) + " |"
        self.assertIn(header, markdown)
        for ident in ("purchased_shaft", "hybrid_pin", "machined_shaft", "printed_shaft"):
            self.assertIn(f"| {ident}: ", markdown)
        self.assertIn("cost 8 EUR (estimate", decision["comparison"][0]["cost_lead_time"])
        self.assertIn("at 10 count", decision["comparison"][0]["cost_lead_time"])

    def test_candidates_for_different_parts_are_separate_decisions(self) -> None:
        documents = scenarios.hot_loaded_bracket()
        documents["parts/TST-0202/part.yaml"] = scenarios.made_part("TST-0202", "Cover", "fdm", "tst_polymer_a_printed", "tst_printer")
        documents["manufacturing/selection.yaml"]["candidates"].append(scenarios.candidate(
            "printed_cover", "Printed cover", "one printed part",
            [scenarios.cpart("TST-0202", "tst_polymer_a_printed", scenarios.step("fdm", "tst_printer", "local"))], cost=2))
        report = assess(self, documents)
        self.assertEqual([d["parts"] for d in report["decisions"]], [["TST-0201"], ["TST-0202"]])
        self.assertEqual(report["decisions"][1]["recommendation"]["primary"], "printed_cover")

    def test_a_part_only_an_eliminated_alternative_covers_stays_unselected(self) -> None:
        """The printed alternative also proposes the cover TST-0202, but it is eliminated; the chosen machined bracket
        names only TST-0201, so no chosen candidate covers the cover."""
        documents = scenarios.hot_loaded_bracket()
        documents["parts/TST-0202/part.yaml"] = scenarios.made_part("TST-0202", "Cover", "fdm", "tst_polymer_a_printed", "tst_printer")
        selection_candidate(documents, "printed_bracket")["parts"].append(
            scenarios.cpart("TST-0202", "tst_polymer_a_printed", scenarios.step("fdm", "tst_printer", "local")))
        report = assess(self, documents)
        decision = only_decision(report)
        self.assertEqual((decision["parts"], decision["recommendation"]["primary"]), (["TST-0201", "TST-0202"], "machined_bracket"))
        self.assertEqual(report["coverage"], {"made": ["TST-0201", "TST-0202"], "unselected": ["TST-0202"]})

    def test_coverage_follows_the_confirmed_primary_not_the_ranking_winner(self) -> None:
        """The ranking winner printed_cover also makes the clip TST-0302, but selection.yaml confirms machined_cover, which
        names only TST-0301. The confirmed choice decides coverage, so the clip is unselected."""
        documents = scenarios.qualified_polymer_cover()
        documents["parts/TST-0302/part.yaml"] = scenarios.made_part("TST-0302", "Clip", "fdm", "tst_polymer_c_printed", "tst_printer")
        selection_candidate(documents, "printed_cover")["parts"].append(
            scenarios.cpart("TST-0302", "tst_polymer_c_printed", scenarios.step("fdm", "tst_printer", "local")))
        recorded = documents["manufacturing/selection.yaml"]
        recorded["status"] = "confirmed"
        recorded["assessment"]["decision"] = "DR-001"
        recorded["recommendation"] = {"status": "final", "primary": "machined_cover", "reasons": ["TST: the user prefers metal"],
                                      "limits": ["TST: none recorded"], "what_would_change": ["TST: nothing recorded"]}
        report = assess(self, documents)
        self.assertEqual(only_decision(report)["recommendation"]["primary"], "printed_cover")
        self.assertEqual(report["coverage"], {"made": ["TST-0301", "TST-0302"], "unselected": ["TST-0302"]})

    def test_every_material_property_has_one_comparison_group(self) -> None:
        grouped = [name for _, names in selection.PROPERTY_GROUPS for name in names]
        self.assertEqual(sorted(grouped), sorted(mi.MATERIAL_PROPERTIES))


class FuFyRatioRequirement(unittest.TestCase):
    """REQ-001 asks for fu/fy >= 1.1 of the cover. A ratio is accepted as a dimensionless number or as percent, and
    like elongation it is tested at a temperature, so a value from another temperature cannot pass at the operating one."""

    def documents(self, polymer: list[dict[str, Any]], metal_ratio: float = 1.15) -> dict[str, Any]:
        documents = scenarios.qualified_polymer_cover()
        requirement = next(r for r in documents["requirements.yaml"]["requirements"] if r["id"] == "REQ-001")
        requirement["acceptance"] = [fx.criterion("fu_fy_ratio", ">=", 1.1, "dimensionless")]
        printed = profile(documents, "materials", "tst_polymer_c_printed")
        printed["properties"] = polymer + printed["properties"][2:]
        profile(documents, "materials", "tst_metal_d_bar")["properties"].append(
            scenarios.prop("fu_fy_ratio", metal_ratio, "dimensionless", temperature=(0, 150)))
        return documents

    def ratio(self, value: float, units: str, **extra: Any) -> list[dict[str, Any]]:
        return [scenarios.prop("fu_fy_ratio", value, units, orientation=o, **extra) for o in ("XY", "Z")]

    def test_a_ratio_passes_as_a_number_or_as_percent(self) -> None:
        report = assess(self, self.documents(self.ratio(110, "percent")))
        for ident, shown in (("printed_cover", "110 percent"), ("machined_cover", "1.15 dimensionless")):
            with self.subTest(ident):
                entry = candidate(report, ident)
                (verdict,) = evaluations(entry, "REQ-001")
                self.assertEqual((entry["status"], verdict["outcome"]), ("feasible", "pass"))
                self.assertIn(f"{shown} against fu_fy_ratio >= 1.1 dimensionless", verdict["reasons"][0])
        strength = only_decision(report)["comparison"][0]["service_properties"]
        self.assertIn("strength: TST-0301 fu_fy_ratio 110 percent (supplier_typical, dry, XY, at 0..60 deg_C)", strength)
        self.assertIn("fu_fy_ratio 1.15 dimensionless", only_decision(report)["comparison"][1]["service_properties"])

    def test_a_ratio_below_the_limit_fails_the_candidate(self) -> None:
        report = assess(self, self.documents(self.ratio(105, "percent")))
        printed = candidate(report, "printed_cover")
        (verdict,) = evaluations(printed, "REQ-001")
        self.assertEqual((printed["status"], verdict["outcome"]), ("eliminated", "fail"))
        self.assertEqual(candidate(report, "machined_cover")["status"], "feasible")

    def test_a_ratio_tested_at_another_temperature_does_not_pass_at_the_operating_temperature(self) -> None:
        report = assess(self, self.documents(self.ratio(1.2, "dimensionless", temperature=(23, 23))))
        printed = candidate(report, "printed_cover")
        self.assertEqual((printed["status"], printed["unknown"]), ("conditional", ["REQ-001"]))
        (verdict,) = evaluations(printed, "REQ-001")
        self.assertIn("15..35 deg_C requested", " ".join(verdict["reasons"]))


class CommandLine(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, check=False)

    def test_exit_status_follows_the_report(self) -> None:
        hw = write(self, scenarios.qualified_polymer_cover())
        result = self.run_cli(str(hw), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "final_ready")
        out = hw.parent / ".cad-work" / "selection" / "report.json"
        result = self.run_cli(str(hw.parent), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["decisions"][0]["recommendation"]["primary"], "printed_cover")
        refused = self.run_cli(str(hw), "--out", str(hw / "report.json"))
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((hw / "report.json").exists())

    def test_an_invalid_or_missing_record_exits_2(self) -> None:
        documents = scenarios.qualified_polymer_cover()
        selection_candidate(documents, "printed_cover")["parts"][0]["material_profile"] = "tst_undeclared"
        hw = write(self, documents)
        result = self.run_cli(str(hw), "--json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual((report["status"], report["decisions"]), ("invalid", []))
        self.assertIn("intent.reference", {p["code"] for p in report["problems"]})
        self.assertEqual(self.run_cli(str(hw.parent / "missing")).returncode, 2)

    def test_a_record_of_an_unsupported_version_is_invalid_and_not_assessed(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-selection-version-")
        self.addCleanup(temporary.cleanup)
        hw = Path(temporary.name) / "hw"
        shutil.copytree(EXAMPLE, hw)
        product = hw / "product.yaml"
        product.write_text(product.read_text(encoding="utf-8").replace("record_version: 2", "record_version: 1"), encoding="utf-8")
        before = tree_digest(hw)
        result = self.run_cli(str(hw), "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["status"], report["record_version"]), ("invalid", 0))
        self.assertIn("record.version_unsupported", {p["code"] for p in report["problems"]})
        self.assertFalse(report["use_case"]["final_ready"])
        self.assertEqual(tree_digest(hw), before)


class StrictMaterialCriteria(unittest.TestCase):
    """The printed polymer's max_service_temperature is 60 deg_C. A strict `<` or `>` criterion at that limit fails
    where the non-strict one passes, judged through the public assessment."""

    def outcome(self, operator: str, limit: float, *, interval: tuple[float, float] | None = None) -> str:
        documents = scenarios.qualified_polymer_cover()
        documents["requirements.yaml"]["requirements"][1]["acceptance"] = [
            fx.criterion("max_service_temperature", operator, limit, "deg_C")]
        if interval is not None:
            temperature = next(p for p in profile(documents, "materials", "tst_polymer_c_printed")["properties"]
                               if p["name"] == "max_service_temperature")
            temperature["value"]["value"] = fx.quantity(lower=interval[0], upper=interval[1], units="deg_C")
        report = assess(self, documents)
        (entry,) = evaluations(candidate(report, "printed_cover"), "REQ-002")
        return str(entry["outcome"])

    def test_the_limit_itself_fails_a_strict_bound_and_meets_a_non_strict_one(self) -> None:
        cases = [  # (operator, limit, outcome) for a value of exactly 60
            ("<", 60, "fail"), ("<=", 60, "pass"), ("<", 60.001, "pass"), ("<=", 60.001, "pass"), ("<", 59.999, "fail"),
            (">", 60, "fail"), (">=", 60, "pass"), (">", 59.999, "pass"), (">=", 59.999, "pass"), (">", 60.001, "fail"),
        ]
        for operator, limit, outcome in cases:
            with self.subTest(operator=operator, limit=limit):
                self.assertEqual(self.outcome(operator, limit), outcome)

    def test_an_interval_straddling_a_strict_limit_is_unknown(self) -> None:
        for operator in ("<", ">"):
            with self.subTest(operator=operator):
                self.assertEqual(self.outcome(operator, 60, interval=(55, 65)), "unknown")


class StrictOperatorsAreNotInterchangeable(unittest.TestCase):
    def test_an_authored_pass_at_a_non_strict_bound_does_not_satisfy_a_strict_requirement(self) -> None:
        """The vendor's authored pass states `yield_strength >= 100`; a requirement of `> 100` is a different criterion,
        so the claim is a mismatch and never credited."""
        documents = scenarios.mandatory_vs_score()
        documents["requirements.yaml"]["requirements"][0]["acceptance"][0]["operator"] = ">"
        report = assess(self, documents)
        texts = [f["measured"]["text"] for f in findings(report, "selection.evaluation_mismatch")]
        self.assertTrue(any("yield_strength >= 100 MPa" in text for text in texts), texts)


if __name__ == "__main__":
    unittest.main()
