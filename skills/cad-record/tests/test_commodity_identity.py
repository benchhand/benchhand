#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""commodity_identity.py: which structured designations name a commodity item, and how a part record spells one.

The expected texts are worked out from the designation fields (the issue's own example is
`ISO 4762 M4x20, 8.8, A2`)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module  # noqa: E402

ci = load_module("cad_record_commodity_identity", Path(__file__).resolve().parents[1] / "scripts" / "commodity_identity.py")

SCREW = {"kind": "standard_designation", "standard": "ISO 4762", "size": "M4", "length": 20, "property_class": "8.8",
         "finish": "A2"}
NUT = {"kind": "standard_designation", "standard": "ISO 4032", "size": "M4", "length": "not_applicable",
       "property_class": "8"}
INSERT = {"kind": "family_designation", "family": "heat_set_insert", "thread": "M3", "length": 5.7,
          "outside_diameter": 4.6, "recommended_hole": 4.0}


def part(designation: Any = None, *, opened: dict | None = None) -> dict[str, Any]:
    purchase: dict[str, Any] = {"identity": {"status": "unknown", "reason": "x"}}
    if designation is not None:
        purchase["designation"] = designation
    record: dict[str, Any] = {"PN": "TST-0001", "intent": {"route": [{"id": "buy", "kind": "purchased", "purchase": purchase}]}}
    if opened:
        record["procurement"] = {"mode": "catalog", "open": opened}
    return record


class Eligibility(unittest.TestCase):
    def test_eligible_designations_have_no_problem(self) -> None:
        for name, designation in (("screw", SCREW), ("nut without a length", NUT), ("insert", INSERT)):
            with self.subTest(name):
                self.assertIsNone(ci.designation_problem(designation))

    def test_an_ineligible_designation_names_its_problem(self) -> None:
        cases = {
            "an unknown family": ({**INSERT, "family": "mystery_widget"}, "family"),
            "a feature-only standard": ({**SCREW, "standard": "ISO 965-1"}, "feature"),
            "a screw without a length": ({**SCREW, "length": "not_applicable"}, "length"),
            "an insert missing its hole": ({k: v for k, v in INSERT.items() if k != "recommended_hole"}, "recommended_hole"),
            "a standard designation missing its size": ({k: v for k, v in SCREW.items() if k != "size"}, "size"),
            "an unknown kind": ({**SCREW, "kind": "manufacturer_pn"}, "kind"),
        }
        for name, (designation, word) in cases.items():
            with self.subTest(name):
                self.assertIn(word, ci.designation_problem(designation) or "")

    def test_a_nut_may_omit_its_length_and_a_dowel_may_not(self) -> None:
        dowel = {**NUT, "standard": "ISO 8734", "size": "6"}
        self.assertIsNone(ci.designation_problem(NUT))
        self.assertIn("length", ci.designation_problem(dowel) or "")


class Text(unittest.TestCase):
    def test_a_standard_designation_reads_like_an_order_line(self) -> None:
        self.assertEqual(ci.designation_text(SCREW), "ISO 4762 M4x20, 8.8, A2")
        self.assertEqual(ci.designation_text(NUT), "ISO 4032 M4, 8")

    def test_a_family_designation_lists_its_dimensions(self) -> None:
        self.assertEqual(ci.designation_text(INSERT), "heat_set_insert M3x5.7, OD 4.6, hole 4")


class PartRecord(unittest.TestCase):
    def test_a_part_with_an_eligible_designation_has_a_commodity_label(self) -> None:
        self.assertEqual(ci.commodity_label(part(SCREW)), "ISO 4762 M4x20, 8.8, A2")

    def test_no_label_without_a_structured_designation_or_with_an_ineligible_one(self) -> None:
        self.assertIsNone(ci.commodity_label(part()))
        self.assertIsNone(ci.commodity_label(part({**SCREW, "length": "not_applicable"})))

    def test_an_open_identity_field_withholds_the_label(self) -> None:
        self.assertIsNone(ci.commodity_label(part(SCREW, opened={"manufacturer_part_number": "not chosen"})))

    def test_the_designation_is_read_from_the_purchased_route_operation_only(self) -> None:
        record = part(SCREW)
        record["intent"]["route"][0]["kind"] = "inspection"
        self.assertIsNone(ci.commodity_label(record))
        self.assertIsNone(ci.commodity_label({"PN": "TST-0001", "intent": {"route": {"status": "unknown"}}}))
        self.assertEqual(ci.commodity_label(copy.deepcopy(part(INSERT))), "heat_set_insert M3x5.7, OD 4.6, hole 4")


if __name__ == "__main__":
    unittest.main()
