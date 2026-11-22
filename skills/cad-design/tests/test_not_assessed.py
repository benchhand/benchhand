# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The not_assessed outcome (D36, D37): with the calibration flag off, a check that needs a calibration and has no generic
limit is not assessed with the reason calibration_off, and a check of a process with no capability profile is not assessed
with the reason no_capability_profile. With the flag on every one of these stays UNKNOWN.

Run: python3 -m unittest discover -s skills/cad-design/tests -p test_not_assessed.py
Needs PyYAML and jsonschema. The end-to-end class also needs uv: it builds a version and runs form_check.py on it.
Every case passes the flag value to `assess` (calibration=), so the tests do not depend on the process environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_process_checks as T  # noqa: E402

PN = "TST-0101"
OFF_REASON = "calibration_off"
NOTE = "the tool does not assess them"  # the end of the sentence every not_assessed finding of a missing limit carries
SHEET_PN = "TST-0902"


def _fdm(documents: dict[str, Any], data: dict[str, Any], obligation: str, code: str) -> tuple[Any, ...]:
    return documents, T.geometry(PN, data), obligation, code


def _bore() -> dict[str, Any]:
    critical = [T.surface("bore", "locating", criteria=[T._diameter_criterion(">= 6")])]
    return T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT], critical=critical)


def _seat() -> dict[str, Any]:
    seat = T.facts(faces=(T.face("f1", features=("seat",), bed=True, normals=((0, 0, -1),)),),
                   walls={"thickness_mm": 3.0, "location_mm": [1.0, 1.0, 1.0], "features": [], "measured": 12})
    seat["bed"]["contact_area_mm2"] = 100.0
    return seat


def _wall_feature_class() -> tuple[dict[str, Any], dict[str, Any]]:
    data = T.facts(walls={"thickness_mm": 1.0, "location_mm": [1.0, 1.0, 1.0], "features": ["tip"], "measured": 12},
                   features={"tip": {"kind": "tooth_tip", "faces": [], "mapped": True}})
    return T.guided(), data


def _bend_facts(*, holes: bool = False) -> dict[str, Any]:
    shape = T.facts(frame="model", holes=(T.hole(None, 3.0),) if holes else ())
    shape["bends"] = [{"inner_radius_mm": 2.0, "thickness_mm": 1.5, "axis": [0.0, 1.0, 0.0], "axis_point": [0.0, 0.0, 0.0],
                       "features": []}]
    return shape


def _casting_facts() -> dict[str, Any]:
    visible = {"+pull": 1.0, "-pull": 0.0}
    return T.facts(frame="model", faces=(T.face("f1", visible=visible, normals=((1.0, 0.0, 0.0),)),
                                         T.face("f2", visible=visible, normals=((0.0, 0.0, 1.0),))))


def _laser_thermal() -> dict[str, Any]:
    documents = T.FX.record(parts=[T.cut(SHEET_PN)], materials=T.materials(),
                            capabilities=[T.laser_profile(T.ACRYLIC_APPROVAL, calibrations=[T.KERF])])
    documents[f"parts/{SHEET_PN}/part.yaml"]["intent"]["critical_surfaces"] = [
        T.surface("datum_a", "datum", criteria=[T.FX.criterion("flatness", "<=", 0.3, "mm")])]
    return documents


def _joined() -> dict[str, Any]:
    joiner = T.FX.capability_profile("tst_joiner", "joining", calibrations=[], materials=[T.approval(T.ACRYLIC)])
    documents = T.FX.record(parts=[T.cut(SHEET_PN)], materials=T.materials(),
                            capabilities=[T.laser_profile(T.ACRYLIC_APPROVAL, calibrations=[T.KERF]), joiner])
    documents[f"parts/{SHEET_PN}/part.yaml"]["intent"]["route"] += [T.FX.operation("join", "joining", "tst_joiner")]
    return documents


def _ungauged() -> dict[str, Any]:
    """The multi-process route with a gauge whose measurement uncertainty is not calibrated."""
    documents = T.multiprocess(complete=True)
    for profile in documents["manufacturing/capabilities.yaml"]["profiles"]:
        if profile["id"] == "tst_gauge":
            profile["calibrations"] = []
    return documents


def _receiving() -> dict[str, Any]:
    """A printed TST-0101 with a bore that receives a press-fitted pin of TST-0102, and no calibrated bore deviation."""
    documents = T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT])
    interface = T.FX.interface("IF-001", PN, "TST-0102", "mounting", {
        "critical": T.known(False), "functions": [{"function": "locating"}],
        "surfaces": [{"PN": PN, "feature": "bore", "role": "locating", "dimension_state": "assembled"},
                     {"PN": "TST-0102", "feature": "pin", "role": "locating", "dimension_state": "assembled"}]})
    documents["interfaces.yaml"] = {"interfaces": [interface]}
    documents["parts/TST-0102/part.yaml"] = T.FX.part("TST-0102")
    documents["assembly.yaml"]["subassemblies"][0]["parts"].append("TST-0102")
    documents["assembly.yaml"]["operations"] = [{"id": "fit_pin", "kind": "press_fit", "part": "TST-0102", "into": PN,
                                                 "interface": "IF-001", "direction": [0, 0, -1],
                                                 "acceptance": {"kind": "seated", "features": ["bore"]}}]
    return documents


def _waterjet() -> dict[str, Any]:
    edge = [T.surface("cut_edge", "seal", criteria=[T.FX.criterion("surface_roughness", "<=", 3.2, "um")])]
    return T.shaped("waterjet", [], critical=edge)


def _cases() -> dict[str, Callable[[], tuple[Any, ...]]]:
    """name: a builder of (documents, geometry or None, obligation, finding code[, guideline catalog])."""
    def contact() -> tuple[Any, ...]:
        return _fdm(T.printed(PN, {"strategy": "none"}, calibrations=[]), T.stable([2.0, 2.0], T.TRIANGLE),
                    f"process.fdm.orientation@{PN}/print", "process.fdm.contact_area_uncalibrated")

    def warp() -> tuple[Any, ...]:
        return _fdm(T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT]),
                    T.stable([2.0, 2.0], [[0, 0], [10, 0], [10, 12], [0, 12]]), f"process.fdm.thermal_distortion@{PN}/print",
                    "process.fdm.warp_uncalibrated")

    def feature_class() -> tuple[Any, ...]:
        documents, data = _wall_feature_class()
        return (*_fdm(documents, data, T.GEOMETRY, "process.fdm.wall_feature_class"), T.SHIPPED)  # a guideline limit the wall breaks

    return {
        "fdm wall": lambda: _fdm(T.guided(), T.walled(3.0), T.GEOMETRY, "process.fdm.wall_uncalibrated"),
        "fdm small feature": lambda: _fdm(T.guided(), T.walled(3.0, pins=(1.0,)), T.GEOMETRY,
                                          "process.fdm.small_feature_uncalibrated"),
        "fdm small hole": lambda: _fdm(T.guided(), T.walled(3.0, holes=(1.0,)), T.GEOMETRY, "process.fdm.small_hole_uncalibrated"),
        "fdm overhang": lambda: _fdm(T.printed(PN, {"strategy": "none"}, calibrations=[]), T.SHELF, T.SUPPORT,
                                     "process.fdm.overhang_uncalibrated"),
        "fdm bed contact area": contact,
        "fdm warp": warp,
        "fdm critical bore": lambda: _fdm(_bore(), T.facts(holes=(T.hole("bore", 6.0),), walls={"thickness_mm": 3.0}), T.GEOMETRY,
                                          "process.fdm.hole_uncalibrated"),
        "fdm critical feature": lambda: _fdm(T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT],
                                                       critical=[T.surface("seat", "locating")]), _seat(), T.GEOMETRY,
                                             "process.fdm.feature_uncalibrated"),
        "fdm first-layer flare": lambda: _fdm(T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT],
                                                        critical=[T.surface("seat", "locating")]), _seat(), T.GEOMETRY,
                                              "process.fdm.elephant_foot"),
        "fdm exempt wall class": feature_class,
        "fdm support on a critical surface": lambda: _fdm(
            T.printed(PN, {"strategy": "none"}, calibrations=[], critical=[T.surface("underside", "locating")]), T.underside(60.0),
            T.SUPPORT, "process.fdm.support_on_critical_unknown"),
        "cnc precision": lambda: (T.FX.record(parts=[T.milled("TST-0201", [T.end_mill(4.0, 25.0)], critical=[
            T.surface("pocket_floor", "seal", criteria=[T.FX.criterion("surface_roughness", "<=", 3.2, "um")])])],
            capabilities=[T.FX.capability_profile("tst_mill", "cnc", parameters={"axes": T.known(T.mm(3, units="count"))},
                                                  materials=[T.approval(T.ALU, operations=["machine"])], calibrations=[])],
            materials=T.materials()), T.geometry("TST-0201", T.POCKET), "process.cnc.precision@TST-0201/mill",
            "process.cnc.precision_uncalibrated"),
        "laser kerf": lambda: (T.tab_slot(tab_thickness=T.mm(lower=2.9, upper=3.1), coating=None, calibrations=[]),
                               T.geometry("TST-0301", T.SLOT), "process.laser.kerf@TST-0301/cut", "process.laser.kerf_uncalibrated"),
        "laser heat-affected zone": lambda: (_laser_thermal(), None, f"process.laser.thermal@{SHEET_PN}/cut",
                                             "process.laser.haz_uncalibrated"),
        "laser flatness": lambda: (_laser_thermal(), None, f"process.laser.thermal@{SHEET_PN}/cut",
                                   "process.laser.flatness_uncalibrated"),
        "fdm receiving feature": lambda: _fdm(_receiving(), T.facts(holes=(T.hole("bore", 6.0),), walls={"thickness_mm": 3.0}),
                                              T.GEOMETRY, "process.fdm.receiving_feature"),
        "waterjet taper": lambda: (_waterjet(), None, f"process.waterjet.edge@{SHEET_PN}/form", "process.waterjet.taper_uncalibrated"),
        "laser smallest hole": lambda: (T.shaped("laser", []), T.geometry(SHEET_PN, T.facts(frame="model", holes=(T.hole(None, 2.0),))),
                                        f"process.laser.features@{SHEET_PN}/form", "process.laser.min_hole_uncalibrated"),
        "laser narrowest web": lambda: (T.shaped("laser", []), T.geometry(SHEET_PN, T.facts(frame="model", walls={"thickness_mm": 1.0})),
                                        f"process.laser.features@{SHEET_PN}/form", "process.laser.min_web_uncalibrated"),
        "sheet-forming hole to bend": lambda: (T.shaped("sheet_forming", []), T.geometry(SHEET_PN, _bend_facts(holes=True)),
                                               f"process.sheet_forming.bend@{SHEET_PN}/form",
                                               "process.sheet_forming.hole_to_bend_uncalibrated"),
        "sheet-forming bend allowance": lambda: (T.shaped("sheet_forming", []), None,
                                                 f"process.sheet_forming.distortion@{SHEET_PN}/form",
                                                 "process.sheet_forming.bend_allowance_uncalibrated"),
        "waterjet edge finish": lambda: (_waterjet(), None, f"process.waterjet.edge@{SHEET_PN}/form",
                                         "process.waterjet.edge_finish_uncalibrated"),
        "casting draft": lambda: (T.shaped("casting", []), T.geometry(SHEET_PN, _casting_facts()),
                                  f"process.casting.draft@{SHEET_PN}/form", "process.casting.draft_uncalibrated"),
        "sheet-forming bend radius": lambda: (T.shaped("sheet_forming", []), T.geometry(SHEET_PN, _bend_facts()),
                                              f"process.sheet_forming.bend@{SHEET_PN}/form",
                                              "process.sheet_forming.bend_uncalibrated"),
        "sheet-forming springback": lambda: (T.shaped("sheet_forming", []), None,
                                             f"process.sheet_forming.distortion@{SHEET_PN}/form",
                                             "process.sheet_forming.springback_uncalibrated"),
        "joining distortion": lambda: (_joined(), None, f"process.joining.distortion@{SHEET_PN}/join",
                                       "process.joining.distortion_uncalibrated"),
        "inspection uncertainty": lambda: (_ungauged(), T.geometry("TST-0501", T.PLATE), "process.inspection.capability@TST-0501/inspect",
                                           "process.inspection.uncertainty_unknown"),
    }


CASES = _cases()


class Assessed(unittest.TestCase):
    """Assessments of fixture records with an explicit flag value; every packet must be schema-valid."""

    def run_case(self, documents: dict[str, Any], data: Any, flag: bool, catalog: Any = None) -> Any:
        record = T.load(self, documents)
        result = pc.assess(record, data or {}, today=T.TODAY, catalog=catalog, calibration=flag)
        self.assertEqual(T.packet_errors(result.packet({"version_manifest": "0" * 64})), [])
        return result

    def row(self, result: Any, obligation: str) -> dict[str, Any]:
        row = result.outcome(obligation)
        self.assertIsNotNone(row, f"{obligation} is not expected: {[o.id for o in result.expected]}")
        assert row is not None
        return row


pc = T.pc


@unittest.skipUnless(T.DEPS, "pyyaml and jsonschema are required")
class MissingLimitEmitterTests(Assessed):
    """Each emitter of a check that has no limit to judge by: not_assessed with the reason calibration_off while the flag
    is off, UNKNOWN with no reason while it is on."""

    def test_off_each_emitter_says_not_assessed_with_the_reason_on_the_row_and_on_the_finding(self) -> None:
        for name, build in CASES.items():
            with self.subTest(name):
                documents, data, obligation, code, *catalog = build()
                result = self.run_case(documents, data, False, *catalog)
                row = self.row(result, obligation)
                self.assertEqual((row["outcome"], row.get("reason")), ("not_assessed", OFF_REASON), result.outcome(obligation))
                found = result.by_code(code)
                self.assertTrue(found, f"{code} not in {sorted({f['code'] for f in result.findings})}")
                self.assertEqual((found[0]["outcome"], found[0]["reason"]), ("not_assessed", OFF_REASON))
                self.assertIn(NOTE, found[0]["consequence"])

    def test_on_each_emitter_stays_unknown_and_carries_no_reason(self) -> None:
        for name, build in CASES.items():
            with self.subTest(name):
                documents, data, obligation, code, *catalog = build()
                result = self.run_case(documents, data, True, *catalog)
                row = self.row(result, obligation)
                self.assertEqual((row["outcome"], "reason" in row), ("unknown", False), result.outcome(obligation))
                found = result.by_code(code)
                self.assertTrue(found, f"{code} not in {sorted({f['code'] for f in result.findings})}")
                self.assertEqual((found[0]["outcome"], "reason" in found[0]), ("unknown", False))
                self.assertNotIn(NOTE, found[0]["consequence"])

    def test_off_a_flat_part_with_no_overhang_limit_is_not_assessed_with_a_finding_that_says_why(self) -> None:
        """No face needs a support, but no limit says so either: the grade of the support pass is not assessed, never a pass."""
        documents = T.printed(PN, {"strategy": "none"}, calibrations=[])
        off = self.run_case(documents, T.geometry(PN, T.underside(0.0)), False)
        row = self.row(off, T.SUPPORT)
        self.assertEqual((row["outcome"], row.get("reason")), ("not_assessed", OFF_REASON))
        found = off.by_code("process.fdm.support_uncalibrated")
        self.assertEqual([(f["outcome"], f["reason"]) for f in found], [("not_assessed", OFF_REASON)])
        on = self.run_case(documents, T.geometry(PN, T.underside(0.0)), True)
        self.assertEqual(self.row(on, T.SUPPORT)["outcome"], "unknown")
        self.assertEqual(on.by_code("process.fdm.support_uncalibrated"), [])

    def test_off_a_resin_support_gap_asks_for_evidence_and_the_flag_on_has_no_such_finding(self) -> None:
        """Resin is additive but outside the flag's process list: its remedy never names a printer calibration."""
        documents = T.shaped("resin", [], material=T.POLYMER, supports={"strategy": "none"})
        data = T.geometry("TST-0902", T.underside(0.0))
        code = "process.resin.support_uncalibrated"
        off = self.run_case(documents, data, False).by_code(code)
        self.assertEqual(len(off), 1)
        self.assertEqual(off[0]["remedy"], "record evidence of overhang_angle for this material and machine "
                                           "(test pieces, a capability study or supplier evidence)")
        on = self.run_case(documents, data, True).by_code(code)
        self.assertEqual([f["remedy"] for f in on], [])  # flag on: the grade has no draft for a resin limit that is merely unknown

    def test_off_a_recorded_calibration_of_another_material_is_refused_and_stays_unknown(self) -> None:
        """A calibration that is on record but unusable is a fixable record problem, not a missing limit: it stays UNKNOWN."""
        stale = T.calibration("kerf_old", "kerf_deviation", T.ALU, -0.05, 0.03, thickness=T.mm(3))  # not for acrylic
        documents = T.tab_slot(tab_thickness=T.mm(lower=2.9, upper=3.1), coating=None, calibrations=[stale])
        result = self.run_case(documents, T.geometry("TST-0301", T.SLOT), False)
        self.assertEqual(self.row(result, "process.laser.kerf@TST-0301/cut")["outcome"], "unknown")

    def test_off_a_calibrated_check_with_no_stated_limit_stays_unknown(self) -> None:
        """The user's own limit is missing from the record: no calibration or flag fills that, so it is UNKNOWN either way."""
        documents = T.FX.record(parts=[T.cut(SHEET_PN)], materials=T.materials(), capabilities=[T.laser_profile(
            T.ACRYLIC_APPROVAL, calibrations=[T.KERF, T.calibration("flat_study", "flatness", T.ACRYLIC, 0.0, 0.3),
                                              T.calibration("haz_study", "heat_affected_zone", T.ACRYLIC, 0.0, 0.2)])])
        documents[f"parts/{SHEET_PN}/part.yaml"]["intent"]["critical_surfaces"] = [T.surface("datum_a", "datum")]
        result = self.run_case(documents, None, False)
        self.assertEqual(self.row(result, f"process.laser.thermal@{SHEET_PN}/cut")["outcome"], "unknown")


def _unprofiled(kind: str = "cnc", capability: Any = None) -> dict[str, Any]:
    """TST-0101 with one operation of `kind` whose capability is unknown (or `capability`), and no profile for it."""
    cap = capability if capability is not None else T.FX.unknown("no capability profile of the machine shop is recorded")
    if kind == "cnc":
        setup = {"stock": {"material_profile": T.ALU, "grade": T.known("TST 6061-T6"), "dimensions": T.known("45 x 35 x 30")},
                 "datums": ["datum_a"], "workholding": {"method": T.known("vice on the datum faces")}}
        route = [T.FX.operation("mill", "cnc", cap, setup=setup)]
        return T.FX.record(parts=[T.part(PN, route, process="cnc", material=T.ALU)], capabilities=[], materials=T.materials())
    route = [T.FX.operation("print", "fdm", cap, output_state="printed", orientation=T.copy.deepcopy(T.APPROVED),
                            supports={"strategy": "none"})]
    return T.FX.record(parts=[T.part(PN, route, process="fdm")], capabilities=[], materials=T.materials(),
                       product_data=T.FX.product(T.FX.use_case()), decisions=("DR-001", "DR-002"))


CNC_GATED = tuple(f"process.cnc.{family}@{PN}/mill" for family in ("stock", "setup", "access", "features", "precision"))


@unittest.skipUnless(T.DEPS, "pyyaml and jsonschema are required")
class NoCapabilityProfileTests(Assessed):
    """A process other than FDM with no capability profile has no generic limits: with the flag off it is not assessed with
    the reason no_capability_profile. The flag on, and an FDM operation, change nothing."""

    def test_off_every_gated_check_and_the_route_check_of_a_cnc_operation_without_a_profile_is_not_assessed(self) -> None:
        result = self.run_case(_unprofiled(), None, False)
        for obligation in (*CNC_GATED, f"route.consistency@{PN}"):
            with self.subTest(obligation):
                row = self.row(result, obligation)
                self.assertEqual((row["outcome"], row.get("reason")), ("not_assessed", "no_capability_profile"))
        gate = result.by_code("process.cnc.capability_unknown")
        self.assertEqual({(f["outcome"], f["reason"]) for f in gate}, {("not_assessed", "no_capability_profile")})
        self.assertIn(NOTE, gate[0]["consequence"])
        self.assertNotIn("calibration", gate[0]["remedy"])
        route = result.by_code("route.capability_unknown")
        self.assertEqual({(f["outcome"], f["reason"]) for f in route}, {("not_assessed", "no_capability_profile")})

    def test_off_a_failing_access_check_stays_a_fail_when_the_operation_has_no_profile(self) -> None:
        """fail ranks ahead of not_assessed: a 6 mm cutter cannot make the R2 pocket corners, whatever the machine's capability."""
        unknown_capability = T.FX.unknown("no capability profile of the machine shop is recorded")
        milled = T.milled(PN, [T.end_mill(6.0, 30.0)], workholding=True, capability=unknown_capability)
        documents = T.FX.record(parts=[milled], capabilities=[], materials=T.materials())
        result = self.run_case(documents, T.geometry(PN, T.POCKET), False)
        self.assertEqual(self.row(result, f"process.cnc.access@{PN}/mill")["outcome"], "fail")
        self.assertEqual(self.row(result, f"process.cnc.stock@{PN}/mill")["outcome"], "not_assessed")
        self.assertTrue(result.by_code("process.cnc.capability_unknown"))

    def test_on_the_same_operation_is_unknown_with_no_reason(self) -> None:
        result = self.run_case(_unprofiled(), None, True)
        for obligation in (*CNC_GATED, f"route.consistency@{PN}"):
            with self.subTest(obligation):
                row = self.row(result, obligation)
                self.assertEqual((row["outcome"], "reason" in row), ("unknown", False))
        self.assertEqual({f["outcome"] for f in result.by_code("process.cnc.capability_unknown")}, {"unknown"})
        self.assertIn("with its calibrations", result.by_code("process.cnc.capability_unknown")[0]["remedy"])

    def test_off_an_fdm_operation_without_a_profile_stays_unknown(self) -> None:
        """Only FDM has guideline limits and the flag: its missing printer profile is a record gap the user fills."""
        result = self.run_case(_unprofiled("fdm"), None, False)
        for family in ("orientation", "geometry", "support", "thermal_distortion"):
            with self.subTest(family):
                row = self.row(result, f"process.fdm.{family}@{PN}/print")
                self.assertEqual((row["outcome"], "reason" in row), ("unknown", False))
        self.assertEqual({f["outcome"] for f in result.by_code("process.fdm.capability_unknown")}, {"unknown"})

    def test_off_an_inspection_with_no_profile_is_not_assessed(self) -> None:
        documents = T.multiprocess(complete=True)
        profiles = documents["manufacturing/capabilities.yaml"]["profiles"]
        documents["manufacturing/capabilities.yaml"]["profiles"] = [p for p in profiles if p["id"] != "tst_gauge"]
        route = documents["parts/TST-0501/part.yaml"]["intent"]["route"]
        for op in route:
            if op["id"] == "inspect":
                op["capability"] = T.FX.unknown("no gauge profile recorded")
                op["inspection"][0].pop("capability")
        result = self.run_case(documents, T.geometry("TST-0501", T.PLATE), False)
        row = self.row(result, "process.inspection.capability@TST-0501/inspect")
        self.assertEqual((row["outcome"], row.get("reason")), ("not_assessed", "no_capability_profile"))


@unittest.skipUnless(T.DEPS, "pyyaml and jsonschema are required")
class NotAssessedSerializationTests(Assessed):
    """A not_assessed result of a real handler carries its reason onto the serialized outcome and finding, where the schema
    requires it, and the packet that holds it is valid, complete and not_assessed (never pass)."""

    def test_the_reason_reaches_the_outcome_the_finding_and_a_valid_packet(self) -> None:
        result = self.run_case(_unprofiled(), None, False)
        gated = [o for o in result.outcomes if o["obligation"] in CNC_GATED]
        self.assertEqual({(o["outcome"], o["reason"]) for o in gated}, {("not_assessed", "no_capability_profile")})
        findings = [f for f in result.findings if f["outcome"] == "not_assessed"]
        self.assertTrue(findings)
        self.assertEqual({f["reason"] for f in findings}, {"no_capability_profile"})
        packet = result.packet({"version_manifest": "0" * 64})
        self.assertEqual((packet["result"], packet["complete"], T.packet_errors(packet)), ("not_assessed", True, []))

    def test_a_fail_in_the_packet_outranks_the_not_assessed_rows(self) -> None:
        documents = T.printed(PN, {"strategy": "none"}, calibrations=[T.CONTACT])
        result = self.run_case(documents, T.geometry(PN, T.stable([8.0, 8.0], T.TRIANGLE)), False)  # off the hull: unstable
        rows = {o["outcome"] for o in result.outcomes}
        self.assertTrue({"fail", "not_assessed"} <= rows, rows)
        self.assertEqual(result.packet({"version_manifest": "0" * 64})["result"], "fail")


@unittest.skipUnless(T.UV and T.DEPS, "uv, pyyaml and jsonschema are required for the kernel build")
class FormCheckEndToEndTests(unittest.TestCase):
    """form_check.py on a built version whose only made part is milled on no recorded capability: with the flag off every
    process check is not assessed, so the packet, the report and the exit code say not_assessed and exit 0, never pass; with
    it on the same version is unknown and exits 1."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        mp = T._projects()
        documents = mp.documents(process="cnc", parts_of=mp.components())
        path = "parts/TST-0001/part.yaml"
        [mill] = documents[path]["intent"]["route"]
        mill["capability"] = T.FX.unknown("no capability profile of the machine shop is recorded")
        mill["setup"] = {"stock": {"material_profile": "tst_polymer", "grade": T.known("TST polymer"),
                                   "dimensions": T.known("45 x 35 x 30")},
                         "datums": ["datum_a"], "workholding": {"method": T.known("vice on the datum faces")}}
        cls.hw = Path(cls.tmp.name) / "project" / "hw"
        mp.shared().write_record(cls.hw, documents)
        mp.add_flow_profile(cls.hw)  # `run` builds only on a settled project-start profile
        mp.set_model(cls.hw, "cadquery", [[60.0, 10.0, 5.0, 2.0]])
        built = subprocess.run(["uv", "run", "--quiet", "--script", str(T.CAD_MODEL), "run", str(cls.hw), "--label", "v1"],
                               capture_output=True, text=True, check=False)
        if built.returncode != 0:
            raise AssertionError(f"cad_model run failed: {built.stdout[-2000:]}{built.stderr[-2000:]}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def form_check(self, flag: bool) -> tuple[int, dict[str, Any]]:
        command = ["uv", "run", "--quiet", "--script", str(T.FORM_CHECK), "--hw", str(self.hw), "--version", "v1",
                   "--fidelity", "F2", "--on", T.TODAY.isoformat(), "--replace"]
        with mock.patch.dict("os.environ"):
            if flag:
                os.environ[T.GL.CALIBRATION_FLAG] = "1"
            else:
                os.environ.pop(T.GL.CALIBRATION_FLAG, None)
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        try:
            return proc.returncode, T.result_payload(proc.stdout)
        except json.JSONDecodeError as error:
            raise AssertionError(f"form_check printed no JSON (exit {proc.returncode}): {proc.stderr[-3000:]}") from error

    def test_off_the_report_says_not_assessed_and_the_exit_code_is_0(self) -> None:
        code, report = self.form_check(False)
        made = report["manufacturing"]
        self.assertEqual((made["status"], made["complete"], code), ("not_assessed", True, 0), report)
        packet = json.loads((Path(made["bundle"]) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual((packet["result"], T.packet_errors(packet)), ("not_assessed", []))
        self.assertNotIn("pass", {o["outcome"] for o in packet["outcomes"] if o["obligation"].startswith("process.cnc.")})
        self.assertEqual({r["reason"] for r in made["not_assessed"]}, {"no_capability_profile"})
        self.assertIn("process.cnc.stock@TST-0001.body/mill", {r["obligation"] for r in made["not_assessed"]})
        self.assertEqual(made["open"], [])

    def test_on_the_same_version_is_unknown_and_the_exit_code_is_1(self) -> None:
        code, report = self.form_check(True)
        self.assertEqual((report["manufacturing"]["status"], code), ("unknown", 1), report)


if __name__ == "__main__":
    unittest.main()
