#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""sourcing: the one predicate that says whether a part record belongs to the delivered product, and
the export group (made, purchased, existing) each part number is placed in."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

sourcing = load("cad_record_sourcing", SCRIPTS / "sourcing.py")


class GroupTests(unittest.TestCase):
    def test_make_buy_names_the_export_group(self) -> None:
        for make_buy, group in (("make", "made"), ("buy", "purchased"), ("existing", "existing")):
            with self.subTest(make_buy=make_buy):
                self.assertEqual(sourcing.group({"PN": "LMP-0001", "make_buy": make_buy}), group)

    def test_a_record_without_a_known_make_buy_is_refused(self) -> None:
        for part in ({"PN": "LMP-0001"}, {"PN": "LMP-0001", "make_buy": "borrow"}):
            with self.subTest(part=part), self.assertRaises(sourcing.SourcingError):
                sourcing.group(part)


class DeliveredTests(unittest.TestCase):
    def test_only_existing_equipment_is_outside_the_delivered_product(self) -> None:
        delivered = {make_buy: sourcing.delivered({"make_buy": make_buy}) for make_buy in ("make", "buy", "existing")}
        self.assertEqual(delivered, {"make": True, "buy": True, "existing": False})

    def test_a_record_without_make_buy_counts_as_delivered(self) -> None:
        self.assertTrue(sourcing.delivered({"PN": "LMP-0001"}))  # validate.py reports the missing make_buy

    def test_groups_of_maps_each_pn_and_lists_the_order(self) -> None:
        parts = {"LMP-0002": {"make_buy": "buy"}, "LMP-0001": {"make_buy": "make"}, "EXT-0001": {"make_buy": "existing"}}
        self.assertEqual(sourcing.groups_of(parts), {"LMP-0002": "purchased", "LMP-0001": "made", "EXT-0001": "existing"})
        self.assertEqual(sourcing.GROUPS, ("made", "purchased", "existing"))


if __name__ == "__main__":
    unittest.main()
