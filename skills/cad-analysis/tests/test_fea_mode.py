#!/usr/bin/env python3
"""The FEA mesh mode a flow profile asks for, and when a fast result must be solved again in accurate mode."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fea_mode import Check, ModeError, escalation, read_mode  # noqa: E402


def product(fea: object) -> dict:
    return {"id": "x", "flow": {"profile": {"fea": fea}}}


class ReadMode(unittest.TestCase):
    def test_an_absent_or_scalar_fea_setting_is_accurate(self) -> None:
        for given in ({}, {"flow": {}}, product("not_asked"), product("on"), product("off"), product(True),
                      product(False), product({"enabled": True})):
            with self.subTest(given=given):
                self.assertEqual((read_mode(given).mode, read_mode(given).band_pct), ("accurate", None))

    def test_the_profile_chooses_fast_and_its_band(self) -> None:
        self.assertEqual(read_mode(product({"enabled": "on", "mode": "fast"})).mode, "fast")
        chosen = read_mode(product({"enabled": True, "mode": "fast", "fast_band_pct": 12.5}))
        self.assertEqual((chosen.mode, chosen.band_pct), ("fast", 12.5))
        self.assertEqual(read_mode(product({"mode": "accurate"})).mode, "accurate")

    def test_a_setting_the_mode_cannot_be_read_from_is_refused(self) -> None:
        cases = {
            "a boolean mode (bare YAML on/off)": {"mode": True},
            "an unknown mode": {"mode": "quick"},
            "a band without fast": {"mode": "accurate", "fast_band_pct": 10},
            "a band of 0": {"mode": "fast", "fast_band_pct": 0},
            "a band above 50": {"mode": "fast", "fast_band_pct": 60},
            "a boolean band": {"mode": "fast", "fast_band_pct": True},
            "an unknown enabled value": {"enabled": "maybe", "mode": "fast"},
        }
        for why, fea in cases.items():
            with self.subTest(why=why), self.assertRaises(ModeError):
                read_mode(product(fea))
        with self.assertRaises(ModeError):
            read_mode(product(3))
        with self.assertRaises(ModeError):  # 1 == True in Python, but it is no YAML switch
            read_mode(product({"enabled": 1, "mode": "fast"}))


class Escalation(unittest.TestCase):
    STRENGTH = 2.0  # yield_safety_factor >= 2
    STIFFNESS = 0.5  # max_displacement <= 0.5 mm

    def decide(self, safety_factor: float, displacement: float) -> tuple[bool, str]:
        return escalation([Check("yield_safety_factor", safety_factor, ">=", self.STRENGTH),
                           Check("max_displacement", displacement, "<=", self.STIFFNESS)], band_pct=10)

    def test_a_margin_inside_the_band_escalates(self) -> None:
        for safety_factor, displacement in ((2.15, 0.2), (1.85, 0.2), (3.0, 0.47), (3.0, 0.54), (2.2, 0.2)):
            with self.subTest(safety_factor=safety_factor, displacement=displacement):
                escalate, reason = self.decide(safety_factor, displacement)
                self.assertTrue(escalate)
                self.assertIn("within 10%", reason)

    def test_a_clear_pass_or_a_clear_fail_stays_fast(self) -> None:
        for safety_factor, displacement in ((3.0, 0.2), (1.2, 0.2), (3.0, 0.9), (1.0, 2.0)):
            with self.subTest(safety_factor=safety_factor, displacement=displacement):
                self.assertFalse(self.decide(safety_factor, displacement)[0])

    def test_a_case_without_a_criterion_stays_fast(self) -> None:
        escalate, reason = escalation([], band_pct=10)
        self.assertFalse(escalate)
        self.assertIn("no acceptance criterion", reason)


if __name__ == "__main__":
    unittest.main()
