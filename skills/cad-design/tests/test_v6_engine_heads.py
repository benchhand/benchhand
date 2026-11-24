#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The V6 engine example's cylinder heads and cam drive (examples/v6-engine): the numbers the design must meet.

Part numbers: VSE-0006 cylinder head, VSE-0007 camshaft, VSE-0008 crank pulley, VSE-0009 cam pulley, VSE-0010 belt (bought).
Expected values come from the spec, not from the code: a four-stroke cam turns once per two crank turns, GT2 has a 2 mm pitch,
the crank piston crown sits 62 mm along the bank axis at TDC (crank radius 14 + rod 48) and 10 mm above the pin axis.

Run: python3 -m unittest discover -s skills/cad-design/tests -p test_v6_engine_heads.py -v
Needs only PyYAML.
"""

from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
HW = REPO / "examples" / "v6-engine" / "hw"
TDC_PIN_DISTANCE_MM = 14.0 + 48.0
MIN_DECK_CLEARANCE_MM = 2.0
CASE_END_X_MM = 60.0  # the crankcase is 120 mm long, centred on the origin
MIN_BELT_CLEARANCE_MM = 2.0
GT2_PITCH_MM = 2.0


def params(pn: str) -> dict:
    """The PARAMS literal of a part's model.py, read without importing a CAD kernel."""
    path = HW / "parts" / pn / "model.py"
    assert path.is_file(), f"{pn} has no model.py"
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "PARAMS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{pn} model.py has no PARAMS")


def occurrences(pn: str) -> list[dict]:
    assembly = yaml.safe_load((HW / "assembly.yaml").read_text(encoding="utf-8"))
    return [o for o in assembly["occurrences"] if o["PN"] == pn]


def axis_of(occ: dict) -> tuple[float, float, float]:
    """The part's local +Z in assembly space for a rotation about X."""
    angle = math.radians(occ["placement"]["rotation"]["angle_deg"])
    return (0.0, -math.sin(angle), math.cos(angle))


class CamDriveTests(unittest.TestCase):
    def test_the_cam_turns_once_per_two_crank_turns(self) -> None:
        crank, cam = params("VSE-0008")["teeth"], params("VSE-0009")["teeth"]
        self.assertEqual(cam / crank, 2)  # the cam pulley carries twice the crank pulley's teeth

    def test_the_belt_is_a_whole_number_of_gt2_teeth(self) -> None:
        belt = params("VSE-0010")
        self.assertEqual(belt["pitch_mm"], GT2_PITCH_MM)
        self.assertEqual(belt["length_mm"], belt["teeth"] * GT2_PITCH_MM)


class HeadClearanceTests(unittest.TestCase):
    def test_one_head_per_bank_and_the_piston_clears_the_deck_at_tdc(self) -> None:
        heads = occurrences("VSE-0006")
        self.assertEqual(len(heads), 2)
        deck = params("VSE-0006")["deck_h"]
        crown = TDC_PIN_DISTANCE_MM + params("VSE-0002")["crown_h"]
        self.assertGreaterEqual(deck - crown, MIN_DECK_CLEARANCE_MM)
        for head in heads:  # each head axis must coincide with a piston bank axis
            axes = [axis_of(p) for p in occurrences("VSE-0002")]
            self.assertTrue(any(math.dist(axis_of(head), a) < 1e-9 for a in axes), head["id"])


class BeltPathTests(unittest.TestCase):
    def test_every_pulley_sits_clear_of_the_crankcase_end(self) -> None:
        pulleys = [o for pn in ("VSE-0008", "VSE-0009") for o in occurrences(pn)]
        self.assertEqual(len(pulleys), 3)  # one crank pulley, two cam pulleys
        for occ in pulleys:
            x = abs(occ["placement"]["translation_mm"][0])
            pn = occ["PN"]
            inner_face = x - params(pn)["width"] / 2.0
            with self.subTest(occ=occ["id"]):
                self.assertGreaterEqual(inner_face - CASE_END_X_MM, MIN_BELT_CLEARANCE_MM)


if __name__ == "__main__":
    unittest.main()
