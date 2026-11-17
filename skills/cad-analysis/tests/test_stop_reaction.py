#!/usr/bin/env python3
"""Stop reaction (engineering.stop_reaction): the reaction of a latch lever's stops derived from the modelled
contact, on the stop_fixture.py geometry. The latched stop is touched across arms 7..13 mm, the open stop across
arms 0..7 mm, under a 30 N handle load at the lever's farthest point (80 mm along it, 3 mm off its axis line).

Expected figures are worked by hand. M = 30 N * sqrt(80^2 + 3^2) mm = 2401.687 N*mm.
Latched: nearest edge M / 7 = 343.098 N, uniform pressure M / 10 = 240.169 N, compliant stop M * 60 / 618 = 233.171 N.
Open: its nearest edge is on the axis, so the rigid reaction is unbounded; uniform M / 3.5 = 686.196 N and
compliant M * 24.5 / 114.333 = 514.647 N are estimates.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
SCRIPT = HERE.parent / "scripts" / "stop_reaction.py"
FIXTURES = HERE / "fixtures"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

fx = load("cad_record_mi_fixtures", SKILLS / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")

JOINT = {"id": "J-001", "type": "revolute", "parent": "TST-0301", "child": "TST-0302", "axis": [0, -1, 0],
         "origin": [0, 0, 0], "min": 0, "max": 75, "nominal": 0}


def load_entry(ident: str, newtons: float = 30.0) -> dict:
    return {"id": ident, "kind": "static", "magnitude": fx.known(fx.quantity(newtons, "N")), "direction": [0, 0, -1],
            "requirements": ["REQ-001"]}


def documents(contact_load: float | None = None, joint: dict | None = None, kind: str = "other",
              limits: dict | None = None) -> dict:
    """A record of the fixture parts: IF-006 the lever-to-bracket stops, critical and structural, under two 30 N loads.
    `joint` replaces the pivot, `kind` is the interface type and `limits` its declared fits."""
    functional = {"critical": fx.known(True), "functions": [{"function": "locating"}, {"function": "structural"}],
                  "loads": ["handle_close_at_latched_stop", "handle_open_at_open_stop"]}
    if contact_load is not None:
        unknown = {"status": "unknown", "reason": "not needed by the fixture"}
        functional["contact"] = {"materials": [unknown, unknown], "load": fx.known(fx.quantity(contact_load, "N"))}
    use = fx.use_case("draft", loads=[load_entry("handle_close_at_latched_stop"), load_entry("handle_open_at_open_stop")])
    docs = fx.record(product_data=fx.product(use),
                     parts=[fx.part("TST-0301", "Bracket", process="cnc"), fx.part("TST-0302", "Lever", process="cnc")],
                     interfaces=[fx.interface("IF-006", "TST-0302", "TST-0301", kind, functional,
                                              **({"limits": limits} if limits else {}))])
    docs["assembly.yaml"]["joints"] = [dict(joint or JOINT)]
    return docs


def validator(definition: str):
    return fx.ref_validator(f"https://srobroek.com/cad/schemas/manufacturing.schema.json#/$defs/{definition}")


class StopGeometryTests(unittest.TestCase):
    """stop_reaction.py on built geometry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name).resolve()
        build = subprocess.run(["uv", "run", "--quiet", "--script", str(FIXTURES / "stop_fixture.py"),
                                str(cls.root / "geometry")], capture_output=True, text=True, timeout=900, check=False)
        if build.returncode != 0:
            raise RuntimeError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.folder.cleanup()

    @classmethod
    def built(cls, gap: float) -> Path:
        """The fixture parts with the latched stop `gap` mm below the lever, built once."""
        folder = cls.root / f"geometry-{gap}"
        if not folder.exists():
            build = subprocess.run(["uv", "run", "--quiet", "--script", str(FIXTURES / "stop_fixture.py"), str(folder), str(gap)],
                                   capture_output=True, text=True, timeout=900, check=False)
            if build.returncode != 0:
                raise RuntimeError(build.stdout + build.stderr)
        return folder

    def run_stop(self, name: str, contact_load: float | None = None, joint: dict | None = None,
                 kind: str = "other", gap: float | None = None, limits: dict | None = None) -> tuple[int, dict]:
        hw = fx.write_record(self.root / name / "hw", documents(contact_load, joint, kind, limits))
        geometry = self.root / "geometry" if gap is None else self.built(gap)
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(SCRIPT), "--hw", str(hw), "--parts-dir",
                               str(geometry / "parts")], capture_output=True, text=True, timeout=1800, check=False)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        finding, outcome = validator("finding"), validator("obligation_outcome")
        for item in result["findings"]:
            self.assertEqual(list(finding.iter_errors(item)), [], item["id"])
        for row in result["outcomes"]:
            self.assertEqual(list(outcome.iter_errors(row)), [], row["obligation"])
        self.assertEqual([o["id"] for o in result["expected"]], ["engineering.stop_reaction@IF-006"])
        return proc.returncode, result

    def test_the_latched_stop_is_bounded_by_its_nearest_edge_and_the_open_stop_is_unbounded(self) -> None:
        code, result = self.run_stop("modelled")
        ends = result["detail"]["interfaces"]["IF-006"]["ends"]
        latched, opened = ends["min"], ends["max"]
        self.assertEqual((latched["touching"], opened["touching"]), (True, True))
        self.assertAlmostEqual(latched["farthest_point_from_axis_mm"], 80.0562, places=3)
        self.assertAlmostEqual(latched["patch"]["min_arm_mm"], 7.0, places=6)
        self.assertAlmostEqual(latched["patch"]["max_arm_mm"], 13.0, places=6)
        reaction = latched["reaction"]
        self.assertEqual(reaction["status"], "bounded")
        self.assertAlmostEqual(reaction["design_reaction_n"], 343.098, places=2)
        self.assertGreaterEqual(reaction["design_reaction_n"], 343.0)
        self.assertAlmostEqual(reaction["uniform_pressure_n"], 240.169, places=2)
        self.assertAlmostEqual(reaction["compliant_stop_n"], 233.171, places=2)
        self.assertAlmostEqual(reaction["pin_load_bound_n"], 373.098, places=2)
        self.assertAlmostEqual(opened["patch"]["min_arm_mm"], 0.0, places=6)
        self.assertAlmostEqual(opened["patch"]["max_arm_mm"], 7.0, places=6)
        self.assertEqual(opened["reaction"]["status"], "unbounded")
        self.assertIsNone(opened["reaction"]["design_reaction_n"])
        self.assertAlmostEqual(opened["reaction"]["uniform_pressure_n"], 686.196, places=2)
        self.assertAlmostEqual(opened["reaction"]["compliant_stop_n"], 514.647, places=2)
        [row] = result["outcomes"]
        self.assertEqual((code, result["result"], row["outcome"]), (1, "unknown", "unknown"))
        [unbounded] = result["findings"]
        self.assertEqual((unbounded["code"], unbounded["severity"], unbounded["outcome"]),
                         ("engineering.stop_reaction_unbounded", "critical", "unknown"))
        self.assertIn("FEA contact case", unbounded["remedy"])
        self.assertIn("design record", unbounded["remedy"])

    def test_a_recorded_240_n_is_understated_against_the_latched_stop(self) -> None:
        code, result = self.run_stop("recorded", contact_load=240.0)
        self.assertEqual((code, result["result"]), (1, "fail"))
        codes = {f["code"]: f for f in result["findings"]}
        self.assertEqual(set(codes), {"engineering.stop_reaction_understated", "engineering.stop_reaction_unbounded"})
        understated = codes["engineering.stop_reaction_understated"]
        self.assertEqual((understated["severity"], understated["outcome"]), ("major", "fail"))
        self.assertEqual(understated["measured"], {"value": 240.0, "units": "N"})
        self.assertAlmostEqual(understated["criterion"]["value"], 343.098, places=2)

    def test_a_recorded_figure_that_covers_the_latched_stop_leaves_only_the_open_stop_unbounded(self) -> None:
        code, result = self.run_stop("covered", contact_load=400.0)
        self.assertEqual((code, result["result"]), (1, "unknown"))
        self.assertEqual([f["code"] for f in result["findings"]], ["engineering.stop_reaction_unbounded"])

    def test_a_loaded_mounting_interface_with_no_movable_joint_is_not_a_stop(self) -> None:
        fixed = {**JOINT, "type": "fixed", "min": 0, "max": 0}
        code, result = self.run_stop("mounting", joint=fixed, kind="mounting")
        [row] = result["outcomes"]
        self.assertEqual((code, result["result"], row["outcome"], result["findings"]), (0, "pass", "not_applicable", []))
        self.assertIn("no movable joint", row["coverage"])

    def test_a_pivot_whose_limits_touch_nothing_is_not_a_stop(self) -> None:
        free = {**JOINT, "min": 10, "max": 30, "nominal": 10}
        code, result = self.run_stop("free-swing", joint=free)
        [row] = result["outcomes"]
        self.assertEqual((code, result["result"], row["outcome"], result["findings"]), (0, "pass", "not_applicable", []))
        self.assertIn("not in contact at either limit of J-001", row["coverage"])
        ends = result["detail"]["interfaces"]["IF-006"]["ends"]
        self.assertEqual((ends["min"]["touching"], ends["max"]["touching"]), (False, False))

    # The latched stop stands `gap` mm below the lever. The pivot stops at 30 deg, well clear of the open stop.
    LATCHED_ONLY = {**JOINT, "max": 30}

    def latched_end(self, result: dict) -> dict:
        return result["detail"]["interfaces"]["IF-006"]["ends"]["min"]

    def test_a_limit_a_tenth_of_a_millimetre_off_is_still_the_stop(self) -> None:
        code, result = self.run_stop("gap-0.1", joint=self.LATCHED_ONLY, gap=0.1)
        latched = self.latched_end(result)
        self.assertAlmostEqual(latched["gap_mm"], 0.1, places=6)
        self.assertEqual(latched["reaction"]["status"], "bounded")
        self.assertAlmostEqual(latched["reaction"]["design_reaction_n"], 343.098, places=2)
        self.assertEqual((code, result["result"]), (0, "pass"))
        self.assertEqual(result["detail"]["interfaces"]["IF-006"]["contact_tolerance"], {"mm": 0.15, "source": "constant"})

    def test_a_limit_half_a_millimetre_off_is_not_a_stop(self) -> None:
        code, result = self.run_stop("gap-0.5", joint=self.LATCHED_ONLY, gap=0.5)
        [row] = result["outcomes"]
        self.assertEqual((code, row["outcome"], result["findings"]), (0, "not_applicable", []))
        evidence = result["detail"]["interfaces"]["IF-006"]
        self.assertAlmostEqual(evidence["ends"]["min"]["gap_mm"], 0.5, places=6)
        self.assertEqual(evidence["contact_tolerance"], {"mm": 0.15, "source": "constant"})
        self.assertIn("tolerance 0.15 mm", row["coverage"])

    def test_a_declared_fit_clearance_above_the_constant_raises_the_tolerance(self) -> None:
        # 20 H11/c11: ES 0.130 and ei -0.240 (ISO 286, 18 to 30 mm), so the largest clearance is 0.370 mm.
        fit = {"running_fit": {"fit": "20 H11/c11", "pair": ["TST-0302", "TST-0301"]}}
        _, plain = self.run_stop("gap-0.3-plain", joint=self.LATCHED_ONLY, gap=0.3)
        self.assertEqual(plain["outcomes"][0]["outcome"], "not_applicable", "0.3 mm is above the 0.15 mm constant")
        code, result = self.run_stop("gap-0.3-fit", joint=self.LATCHED_ONLY, gap=0.3, limits=fit)
        latched = self.latched_end(result)
        self.assertAlmostEqual(latched["gap_mm"], 0.3, places=6)
        self.assertEqual(latched["reaction"]["status"], "bounded")
        self.assertEqual((code, result["result"]), (0, "pass"))
        tolerance = result["detail"]["interfaces"]["IF-006"]["contact_tolerance"]
        self.assertAlmostEqual(tolerance["mm"], 0.37, places=6)
        self.assertEqual(tolerance["source"], "fit 20 H11/c11 clearance")
        _, beyond = self.run_stop("gap-0.5-fit", joint=self.LATCHED_ONLY, gap=0.5, limits=fit)
        self.assertEqual(beyond["outcomes"][0]["outcome"], "not_applicable", "0.5 mm is above the 0.37 mm clearance")

    def test_a_gap_exactly_at_the_tolerance_is_a_stop(self) -> None:
        code, result = self.run_stop("gap-at-tolerance", joint=self.LATCHED_ONLY, gap=0.15)
        latched = self.latched_end(result)
        self.assertEqual(latched["reaction"]["status"], "bounded")
        self.assertEqual((code, result["result"]), (0, "pass"))
        self.assertEqual(result["detail"]["interfaces"]["IF-006"]["contact_tolerance"], {"mm": 0.15, "source": "constant"})

    def test_a_gap_two_microns_above_the_tolerance_is_not_a_stop(self) -> None:
        code, result = self.run_stop("gap-above-tolerance", joint=self.LATCHED_ONLY, gap=0.152)
        [row] = result["outcomes"]
        self.assertEqual((code, row["outcome"], result["findings"]), (0, "not_applicable", []))

    def test_the_same_boundary_holds_at_a_tolerance_raised_by_a_fit(self) -> None:
        fit = {"running_fit": {"fit": "20 H11/c11", "pair": ["TST-0302", "TST-0301"]}}  # clearance 0.370 mm
        code, at = self.run_stop("fit-at-tolerance", joint=self.LATCHED_ONLY, gap=0.37, limits=fit)
        self.assertEqual(self.latched_end(at)["reaction"]["status"], "bounded")
        self.assertEqual((code, at["result"]), (0, "pass"))
        _, above = self.run_stop("fit-above-tolerance", joint=self.LATCHED_ONLY, gap=0.372, limits=fit)
        self.assertEqual(above["outcomes"][0]["outcome"], "not_applicable")

    def test_a_tilted_face_within_the_tolerance_has_no_parallel_pair_and_stays_unknown(self) -> None:
        # At 0.5 deg the lever's underside is 7 * sin(0.5 deg) = 0.061 mm off the stop at its nearest edge, and tilted.
        tilted = {**JOINT, "min": 0.5, "max": 30, "nominal": 0.5}
        code, result = self.run_stop("tilted", joint=tilted)
        [row] = result["outcomes"]
        self.assertEqual((code, row["outcome"]), (1, "unknown"))
        self.assertIn("no parallel opposite-facing planar faces", row["uncertainty"])
        self.assertAlmostEqual(self.latched_end(result)["gap_mm"], 0.0611, places=3)


if __name__ == "__main__":
    unittest.main()
