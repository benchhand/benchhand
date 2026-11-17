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


def documents(contact_load: float | None = None, joint: dict | None = None, kind: str = "other") -> dict:
    """A record of the fixture parts: IF-006 the lever-to-bracket stops, critical and structural, under two 30 N loads.
    `joint` replaces the pivot and `kind` is the interface type."""
    functional = {"critical": fx.known(True), "functions": [{"function": "locating"}, {"function": "structural"}],
                  "loads": ["handle_close_at_latched_stop", "handle_open_at_open_stop"]}
    if contact_load is not None:
        unknown = {"status": "unknown", "reason": "not needed by the fixture"}
        functional["contact"] = {"materials": [unknown, unknown], "load": fx.known(fx.quantity(contact_load, "N"))}
    use = fx.use_case("draft", loads=[load_entry("handle_close_at_latched_stop"), load_entry("handle_open_at_open_stop")])
    docs = fx.record(product_data=fx.product(use),
                     parts=[fx.part("TST-0301", "Bracket", process="cnc"), fx.part("TST-0302", "Lever", process="cnc")],
                     interfaces=[fx.interface("IF-006", "TST-0302", "TST-0301", kind, functional)])
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

    def run_stop(self, name: str, contact_load: float | None = None, joint: dict | None = None,
                 kind: str = "other") -> tuple[int, dict]:
        hw = fx.write_record(self.root / name / "hw", documents(contact_load, joint, kind))
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(SCRIPT), "--hw", str(hw), "--parts-dir",
                               str(self.root / "geometry" / "parts")], capture_output=True, text=True, timeout=1800, check=False)
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
        free = {**JOINT, "min": 10, "max": 60, "nominal": 10}
        code, result = self.run_stop("free-swing", joint=free)
        [row] = result["outcomes"]
        self.assertEqual((code, result["result"], row["outcome"], result["findings"]), (0, "pass", "not_applicable", []))
        self.assertIn("not in contact at either limit of J-001", row["coverage"])
        ends = result["detail"]["interfaces"]["IF-006"]["ends"]
        self.assertEqual((ends["min"]["touching"], ends["max"]["touching"]), (False, False))


if __name__ == "__main__":
    unittest.main()
