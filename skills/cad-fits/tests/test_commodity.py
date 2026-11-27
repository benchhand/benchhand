#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Commodity hardware table tests. Expected values are the published ISO dimensions
(ISO 4762 M4: dk 7, k 4, s 3; ISO 4032 M5: s 8, m 4.7; ISO 273 medium M6 hole 6.6), read from
https://www.fasteners.eu/standards/ISO/4762/, .../4032/ and https://mechcodex.com/reference/metric-clearance-hole-sizes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module  # noqa: E402

commodity = load_module("cad_commodity", Path(__file__).resolve().parents[1] / "scripts" / "commodity.py")


class CommodityTests(unittest.TestCase):
    def test_socket_head_screw_dimensions_and_length(self) -> None:
        found = commodity.lookup("ISO 4762 M4x20")
        self.assertEqual(found["dimensions"], {"head_diameter_max": 7.0, "head_height_max": 4.0, "key_width": 3.0})
        self.assertEqual(found["length"], 20.0)
        self.assertEqual(found["thread"]["pitch"], 0.7)

    def test_nut_and_clearance_hole(self) -> None:
        found = commodity.lookup("ISO 4032 M5")
        self.assertEqual(found["dimensions"], {"across_flats": 8.0, "height_max": 4.7})
        self.assertEqual(commodity.lookup("ISO 7089 M6")["thread"]["clearance_hole_medium"], 6.6)

    def test_heat_set_insert_is_a_family_not_a_standard(self) -> None:
        found = commodity.lookup("heat_set_insert M3")
        self.assertEqual(found["dimensions"], {"length": 5.7, "outside_diameter": 4.6, "recommended_hole": 4.0})

    def test_knurled_thumb_screw_rows_as_printed_in_din_653(self) -> None:
        # Typed from https://fasteners.eu/standards/din/653 (columns M3, (M3,5), M4, M5, M6, M8 of the dk, k and ds rows),
        # cross-checked against boltport.com/standards/din-653 and engineeringhardware.com; read 2026-11-27.
        fields = ("head_diameter_nominal", "head_diameter_max", "head_diameter_min", "head_height_max",
                  "head_height_min", "shank_diameter_max", "shank_diameter_min")
        printed = {
            "M3": (12, 12.35, 11.65, 2.5, 2.25, 3, 2.86),
            "M3.5": (14, 14.35, 13.65, 3, 2.75, 3.5, 3.32),
            "M4": (16, 16.35, 15.65, 3.5, 3.2, 4, 3.82),
            "M5": (20, 20.42, 19.58, 4, 3.7, 5, 4.82),
            "M6": (24, 24.42, 23.58, 5, 4.7, 6, 5.82),
            "M8": (30, 30.42, 29.58, 6, 5.7, 8, 7.78),
        }
        for size, values in printed.items():
            with self.subTest(size=size):
                self.assertEqual(commodity.lookup(f"DIN 653 {size}")["dimensions"], dict(zip(fields, values, strict=True)))

    def test_knurled_thumb_screw_m4_head_is_16_not_the_m35_value(self) -> None:
        found = commodity.lookup("DIN 653 M4x12")
        self.assertEqual(found["dimensions"]["head_diameter_nominal"], 16)
        self.assertEqual(found["length"], 12.0)
        self.assertEqual(found["thread"]["pitch"], 0.7)

    def test_unknown_designation_or_size_is_refused(self) -> None:
        for text in ("ISO 9999 M4", "ISO 4762 M12", "ISO 7380-1 M2", "bolt", "DIN 653 M12", "DIN 653 M3.6"):
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
