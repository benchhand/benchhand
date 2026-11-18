# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The shipped FDM guideline files (benchhand-3g4): each validates against the guideline schema, ids are
unique and match the file name, every limit is cited to a dated, located source, and nothing the schema
rules out (layer height, shrinkage, Z strength, a press fit) ships."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

import yaml

SKILL = Path(__file__).resolve().parents[1]
GUIDELINES = SKILL / "assets" / "manufacturing" / "guidelines"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

fx = load("cad_record_mi_fixtures", Path(__file__).resolve().parent / "fixtures" / "manufacturing_intent.py")
registry = fx.schema_registry_module()
SCHEMA = "manufacturing-guideline.schema.json"
ACCESSED = "2026-11-18"
EXPECTED_IDS = {
    "fdm.generic",
    "fdm.nozzle.d0_2", "fdm.nozzle.d0_4", "fdm.nozzle.d0_6", "fdm.nozzle.d0_8",
    "fdm.material.pla", "fdm.material.petg", "fdm.material.abs", "fdm.material.asa", "fdm.material.tpu_95a",
    "fdm.material.tpu_85a", "fdm.material.pa", "fdm.material.pc", "fdm.material.pa_cf",
    "fdm.filler.cf", "fdm.filler.gf",
    "fdm.compat.tpu_85a",
    "fdm.machine.bambu_h2d",
}
FORBIDDEN = {"layer_height", "shrinkage", "z_strength", "z_ratio", "press_fit", "percent_deviation"}


def shipped() -> dict[str, dict[str, Any]]:
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(GUIDELINES.glob("*.yaml"))}


def limits_of(guideline_id: str) -> list[dict[str, Any]]:
    return next(doc for doc in shipped().values() if doc["id"] == guideline_id)["limits"]


def limit_of(guideline_id: str, characteristic: str) -> dict[str, Any]:
    return next(item for item in limits_of(guideline_id) if item["characteristic"] == characteristic)


def bare(item: dict[str, Any]) -> tuple[str, Any]:
    quantity = item["quantity"]
    return item["operator"], (quantity["value"], quantity["units"])


class ShippedFiles(unittest.TestCase):
    def test_the_expected_set_ships_and_each_file_is_named_for_its_id(self) -> None:
        files = shipped()
        self.assertEqual({doc["id"] for doc in files.values()}, EXPECTED_IDS)
        self.assertEqual(len(files), len(EXPECTED_IDS), "an id ships twice")
        for name, doc in files.items():
            with self.subTest(name):
                self.assertEqual(name, f"{doc['id']}.yaml")

    def test_every_file_validates_against_the_guideline_schema(self) -> None:
        validator = registry.document_validator(SKILL / "schemas", SCHEMA)
        for name, doc in shipped().items():
            with self.subTest(name):
                self.assertEqual([e.message for e in validator.iter_errors(doc)], [])

    def test_every_extends_names_a_shipped_file(self) -> None:
        for name, doc in shipped().items():
            for parent in doc.get("extends", []):
                with self.subTest(name, parent=parent):
                    self.assertIn(parent, EXPECTED_IDS)

    def test_every_limit_cites_a_dated_located_url_source(self) -> None:
        for name, doc in shipped().items():
            for item in doc["limits"]:
                for citation in item["citations"]:
                    with self.subTest(name, characteristic=item["characteristic"], reference=citation["reference"]):
                        self.assertNotEqual(citation["kind"], "guideline")
                        self.assertTrue(citation["url"].startswith("https://"))
                        self.assertEqual(citation["accessed"], ACCESSED)
                        self.assertTrue(citation["locator"].strip())

    def test_no_forbidden_characteristic_and_no_machine_scope_ships(self) -> None:
        for name, doc in shipped().items():
            with self.subTest(name):
                self.assertFalse(FORBIDDEN & {item["characteristic"] for item in doc["limits"]})
                self.assertNotIn("machine", doc["scope"])


class ShippedValues(unittest.TestCase):
    """Expected values are the survey's figures, written out here apart from the files."""

    def test_the_generic_set_carries_the_surveyed_limits(self) -> None:
        got = {item["characteristic"]: bare(item) for item in limits_of("fdm.generic")}
        self.assertEqual(got, {
            "overhang_angle": ("<=", (45, "deg")),
            "bridge_span": ("<=", (5, "mm")),
            "min_wall": (">=", (0.8, "mm")),
            "min_feature": (">=", (2.0, "mm")),
            "min_hole": (">=", (2.0, "mm")),
        })

    def test_single_source_limits_have_one_citation_and_two_source_limits_two(self) -> None:
        counts = {item["characteristic"]: len(item["citations"]) for item in limits_of("fdm.generic")}
        self.assertEqual(counts, {"overhang_angle": 2, "bridge_span": 1, "min_wall": 2, "min_feature": 1,
                                  "min_hole": 1})

    def test_each_nozzle_set_is_three_line_widths_with_a_floor_of_0_8_mm(self) -> None:
        for diameter in ("0_2", "0_4", "0_6", "0_8"):
            with self.subTest(diameter):
                self.assertEqual(limit_of(f"fdm.nozzle.d{diameter}", "min_wall")["relative"],
                                 {"relative_to": "line_width", "factor": 3, "floor": {"value": 0.8, "units": "mm"}})

    def test_enclosure_is_required_for_the_warping_materials_and_pa_cf_is_disputed(self) -> None:
        for material in ("abs", "asa", "pa", "pc"):
            with self.subTest(material):
                item = limit_of(f"fdm.material.{material}", "enclosure")
                self.assertEqual((item["operator"], item["value"]), ("equals", True))
                self.assertNotIn("disputed", item)
        disputed = limit_of("fdm.material.pa_cf", "enclosure")
        self.assertEqual((disputed["operator"], disputed["value"], disputed["disputed"]), ("equals", True, True))
        self.assertGreaterEqual(len(disputed["citations"]), 2)

    def test_pa_cf_extends_pa_and_the_cf_filler_and_the_fillers_need_hardened_steel(self) -> None:
        self.assertEqual(next(d for d in shipped().values() if d["id"] == "fdm.material.pa_cf")["extends"],
                         ["fdm.material.pa", "fdm.filler.cf"])
        for filler in ("cf", "gf"):
            with self.subTest(filler):
                item = limit_of(f"fdm.filler.{filler}", "nozzle_material")
                self.assertEqual((item["operator"], item["value"]), ("one_of", ["hardened_steel"]))
                self.assertEqual(bare(limit_of(f"fdm.filler.{filler}", "nozzle_diameter")), (">=", (0.4, "mm")))

    def test_tpu_85a_needs_a_nozzle_of_at_least_0_6_mm(self) -> None:
        self.assertEqual(bare(limit_of("fdm.compat.tpu_85a", "nozzle_diameter")), (">=", (0.6, "mm")))


if __name__ == "__main__":
    unittest.main()
