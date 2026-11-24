#!/usr/bin/env python3
"""Commodity hardware table tests. Expected values are the published ISO dimensions
(ISO 4762 M4: dk 7, k 4, s 3; ISO 4032 M5: s 8, m 4.7; ISO 273 medium M6 hole 6.6)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("scripts") / "commodity.py"
SPEC = importlib.util.spec_from_file_location("cad_commodity", SCRIPT)
assert SPEC and SPEC.loader
commodity = importlib.util.module_from_spec(SPEC)
sys.modules["cad_commodity"] = commodity
SPEC.loader.exec_module(commodity)


class CommodityTests(unittest.TestCase):
    def test_socket_head_screw_dimensions_and_length(self) -> None:
        found = commodity.lookup("ISO 4762 M4x20")
        self.assertEqual(found["dimensions"], {"head_diameter_max": 7.0, "head_height": 4.0, "key_width": 3.0})
        self.assertEqual(found["length"], 20.0)
        self.assertEqual(found["thread"]["pitch"], 0.7)

    def test_nut_and_clearance_hole(self) -> None:
        found = commodity.lookup("ISO 4032 M5")
        self.assertEqual(found["dimensions"], {"across_flats": 8.0, "height_max": 4.7})
        self.assertEqual(commodity.lookup("ISO 7089 M6")["thread"]["clearance_hole_medium"], 6.6)

    def test_heat_set_insert_is_a_family_not_a_standard(self) -> None:
        found = commodity.lookup("heat_set_insert M3")
        self.assertEqual(found["dimensions"], {"length": 5.7, "outside_diameter": 4.6, "recommended_hole": 4.0})

    def test_unknown_designation_or_size_is_refused(self) -> None:
        for text in ("ISO 9999 M4", "ISO 4762 M12", "bolt"):
            with self.subTest(text=text), self.assertRaises(commodity.CommodityError):
                commodity.lookup(text)

    def test_every_row_matches_its_field_list(self) -> None:
        entries = {**commodity.table()["standards"], **commodity.table()["families"]}
        for name, entry in entries.items():
            width = len(entry["fields"].split(","))
            for size, values in entry["size"].items():
                with self.subTest(name=name, size=size):
                    self.assertEqual(len(values), width)


if __name__ == "__main__":
    unittest.main()
