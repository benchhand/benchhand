#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""ISO 286 fit tests against published worked examples.

Sources for the expected values (fetched 2026-10-26):
  [W]  Wikipedia, "Engineering fit", worked examples at 50 mm (citing RoyMech):
       https://en.wikipedia.org/w/index.php?title=Engineering_fit&action=raw
  [MM] MetricMech, ISO 286 fits chart, 25 mm rows:
       https://metricmech.com/reference/iso-286-fits-chart
  [FR] Fractory, "Limits and fits", 25 mm H7/g6 and H7/k6 extrema:
       https://fractory.com/limits-and-fits/
  [RT] Rules of Thumb, limits and fits, 50 mm H7/g6 worked example:
       https://rulesofthumb.info/mechanical/limits-fits
  [EK] EKINSUN, limits and fits, 25 mm and 50 mm H7/p6:
       https://www.ekinsun.com/capabilities/limits-and-fits/
  [I2] ISO 286-2:2010 Table 23 (js), as printed in the SIST preview:
       https://preview.sist.si/sist-preview/54915/7d6147c29fed4e31af36b60561e88752/ISO-286-2-2010.pdf
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from decimal import Decimal
from pathlib import Path
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fits.py"
SPEC = importlib.util.spec_from_file_location("cad_fits", SCRIPT)
assert SPEC and SPEC.loader
fits = importlib.util.module_from_spec(SPEC)
sys.modules["cad_fits"] = fits
SPEC.loader.exec_module(fits)

# (size mm, fit, hole min/max mm, shaft min/max mm, max/min clearance um, type, sources)
PUBLISHED = [
    ("25", "H7/g6", (25.000, 25.021), (24.980, 24.993), (41, 7), "clearance", "MM, FR"),
    ("25", "H7/k6", (25.000, 25.021), (25.002, 25.015), (19, -15), "transition", "MM, FR"),
    ("25", "H7/p6", (25.000, 25.021), (25.022, 25.035), (-1, -35), "interference", "MM, EK"),
    ("50", "H7/g6", (50.000, 50.025), (49.975, 49.991), (50, 9), "clearance", "RT, EK"),
    ("50", "H7/k6", (50.000, 50.025), (50.002, 50.018), (23, -18), "transition", "W"),
    ("50", "H7/p6", (50.000, 50.025), (50.026, 50.042), (-1, -42), "interference", "W, EK"),
    ("50", "H8/f7", (50.000, 50.039), (49.950, 49.975), (89, 25), "clearance", "W"),
]


class PublishedExampleTests(unittest.TestCase):
    def test_fits_match_published_worked_examples(self) -> None:
        for size, fit, hole, shaft, clearance, kind, sources in PUBLISHED:
            with self.subTest(size=size, fit=fit, sources=sources):
                result = fits.evaluate(Decimal(size), fit)
                self.assertEqual((result["hole"]["min_mm"], result["hole"]["max_mm"]), hole)
                self.assertEqual((result["shaft"]["min_mm"], result["shaft"]["max_mm"]), shaft)
                self.assertEqual((result["fit"]["max_clearance_um"], result["fit"]["min_clearance_um"]), clearance)
                self.assertEqual(result["fit"]["type"], kind)

    def test_js_keeps_half_micrometre_for_odd_it(self) -> None:
        # [I2] js7 over 18 to 30 mm: IT7 = 21 um, printed as +/-10.5 um.
        shaft = fits.evaluate(Decimal("25"), "js7")["shaft"]
        self.assertEqual((shaft["upper_deviation_um"], shaft["lower_deviation_um"]), (10.5, -10.5))


class TableRuleTests(unittest.TestCase):
    """Rules of ISO 286-1:2010 that the worked examples do not reach."""

    def deviations(self, size: str, cls: str) -> tuple[float, float]:
        entry = fits.evaluate(Decimal(size), cls)
        side = entry.get("shaft") or entry["hole"]
        return side["upper_deviation_um"], side["lower_deviation_um"]

    def test_upper_range_bound_is_inclusive(self) -> None:
        self.assertEqual(self.deviations("30", "H7"), (21, 0))  # 30 belongs to over 18 up to 30
        self.assertEqual(self.deviations("30.001", "H7"), (25, 0))

    def test_k_fundamental_deviation_depends_on_grade(self) -> None:
        self.assertEqual(self.deviations("25", "k7"), (23, 2))  # IT4-IT7: ei = +2
        self.assertEqual(self.deviations("25", "k8"), (33, 0))  # above IT7: ei = 0

    def test_s_uses_the_subdivided_range(self) -> None:
        self.assertEqual(self.deviations("60", "s6"), (72, 53))  # over 50 to 65
        self.assertEqual(self.deviations("70", "s6"), (78, 59))  # over 65 to 80

    def test_out_of_coverage_inputs_are_rejected(self) -> None:
        for size, cls in [("0", "H7"), ("120.5", "H7"), ("25", "H12"), ("25", "e7"), ("25", "G7"), ("25", "g6/H7")]:
            with self.subTest(size=size, cls=cls), self.assertRaises(fits.FitError):
                fits.evaluate(Decimal(size), cls)

    def test_expect_flag_maps_to_exit_codes(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fits.main(["25", "H7/p6", "--expect", "interference"]), 0)
            self.assertEqual(fits.main(["25", "H7/k6", "--expect", "clearance"]), 1)
            self.assertEqual(fits.main(["130", "H7/k6"]), 2)


DESIGN_INTENT = {"kind": "decision", "reference": "crush-rib seat", "decision": "DR-001"}


class DeclaredFitTests(unittest.TestCase):
    """An interface fit entry: an ISO designation or explicit limits, classified by one rule."""

    def test_an_interference_band_on_the_diameter_is_an_interference_fit(self) -> None:
        fit = fits.declared_fit({"nominal_mm": 22, "interference": {"min": 0, "max": 1.013, "units": "mm"},
                                 "source": DESIGN_INTENT})
        self.assertEqual((fit["form"], fit["type"]), ("interference", "interference"))
        # interference 0..1.013 on the diameter is a diametral clearance of -1.013..0
        self.assertEqual((fit["min_clearance_mm"], fit["max_clearance_mm"]), (-1.013, 0))

    def test_a_clearance_band_in_micrometres_is_a_clearance_fit(self) -> None:
        fit = fits.declared_fit({"nominal_mm": 8, "clearance": {"min": 20, "max": 60, "units": "um"},
                                 "source": DESIGN_INTENT})
        self.assertEqual((fit["type"], fit["min_clearance_mm"], fit["max_clearance_mm"]), ("clearance", 0.02, 0.06))

    def test_hole_and_shaft_deviations_classify_as_their_iso_class_does(self) -> None:
        # 25 H7/k6 as limits: hole +21/0 um, shaft +15/+2 um -> clearance -15..+19 um, transition (MM, FR above)
        fit = fits.declared_fit({"nominal_mm": 25, "source": DESIGN_INTENT, "deviations": {
            "units": "um", "hole": {"upper": 21, "lower": 0}, "shaft": {"upper": 15, "lower": 2}}})
        self.assertEqual((fit["type"], fit["min_clearance_mm"], fit["max_clearance_mm"]), ("transition", -0.015, 0.019))
        self.assertEqual((fit["hole"], fit["shaft"]), ({"upper_mm": 0.021, "lower_mm": 0}, {"upper_mm": 0.015, "lower_mm": 0.002}))

    def test_an_iso_designation_classifies_unchanged(self) -> None:
        fit = fits.declared_fit({"fit": "25 H7/p6"})
        self.assertEqual((fit["form"], fit["type"], fit["label"]), ("iso", "interference", "25 H7/p6"))
        self.assertEqual((fit["min_clearance_mm"], fit["max_clearance_mm"]), (-0.035, -0.001))

    def test_a_crush_rib_fit_prints_its_limits_and_ribs(self) -> None:
        fit = fits.declared_fit({"nominal_mm": 22, "interference": {"min": 0, "max": 1.013, "units": "mm"},
                                 "compliance": "crush_rib", "ribs": {"count": 6, "width_mm": 1.2}, "source": DESIGN_INTENT})
        self.assertEqual(fit["compliance"], "crush_rib")
        self.assertEqual(fit["label"], "⌀22 interference 0/1.013, crush ribs (6 x 1.2 wide)")

    def test_a_plain_language_or_malformed_fit_names_the_accepted_forms(self) -> None:
        cases = {"plain language": {"fit": "interference 0 to 1.013 mm, crush ribs"},
                 "no form": {"pair": ["TST-0001", "TST-0002"]},
                 "two forms": {"fit": "22 H7/p6", "nominal_mm": 22, "source": DESIGN_INTENT,
                               "interference": {"min": 0, "max": 1, "units": "mm"}},
                 "band without nominal": {"interference": {"min": 0, "max": 1, "units": "mm"}, "source": DESIGN_INTENT},
                 "inverted band": {"nominal_mm": 22, "source": DESIGN_INTENT,
                                   "interference": {"min": 1, "max": 0, "units": "mm"}},
                 "no source": {"nominal_mm": 22, "interference": {"min": 0, "max": 1, "units": "mm"}},
                 "compliant clearance": {"nominal_mm": 22, "source": DESIGN_INTENT, "compliance": "crush_rib",
                                         "clearance": {"min": 0, "max": 1, "units": "mm"}}}
        for name, entry in cases.items():
            with self.subTest(name), self.assertRaisesRegex(fits.FitError, "accepted forms: fit: 'SIZE HOLE/SHAFT'"):
                fits.declared_fit(entry)

    def test_a_value_of_the_wrong_type_or_a_misspelled_key_names_its_field(self) -> None:
        band = {"min": 0, "max": 1.013, "units": "mm"}
        cases = {
            "list units": ({"nominal_mm": 22, "source": DESIGN_INTENT, "interference": {**band, "units": ["mm"]}},
                           "interference.units"),
            "list compliance": ({"nominal_mm": 22, "source": DESIGN_INTENT, "interference": band,
                                 "compliance": ["crush_rib"]}, "compliance"),
            "text nominal": ({"nominal_mm": "22", "source": DESIGN_INTENT, "interference": band}, "nominal_mm"),
            "text rib count": ({"nominal_mm": 22, "source": DESIGN_INTENT, "interference": band,
                                "compliance": "crush_rib", "ribs": {"count": "6"}}, "ribs.count"),
            "list deviations units": ({"nominal_mm": 5, "source": DESIGN_INTENT, "deviations": {
                "units": ["um"], "hole": {"upper": 12, "lower": 0}, "shaft": {"upper": 20, "lower": 12}}},
                "deviations.units"),
            "misspelled key": ({"fitt": "5 H7/f7", "pair": ["TST-0001", "TST-0002"]}, "unknown key 'fitt'"),
            # the test project's local patch (absolute limits_mm sizes) is not a form: it must migrate, not be ignored
            "local limits_mm": ({"fit": "crush-rib interference", "limits_mm": {
                "hole": [50.987, 51.987], "shaft": [51.987, 52.0], "decision": "DR-042"}}, "unknown key 'limits_mm'"),
            "deviation missing a bound": ({"nominal_mm": 52, "source": DESIGN_INTENT, "deviations": {
                "units": "mm", "hole": {"upper": -0.013}, "shaft": {"upper": 0, "lower": -0.013}}},
                "deviations.hole.lower must be a finite number"),
            "reversed deviation": ({"nominal_mm": 52, "source": DESIGN_INTENT, "deviations": {
                "units": "mm", "hole": {"upper": -1.013, "lower": -0.013}, "shaft": {"upper": 0, "lower": -0.013}}},
                "deviations.hole.lower -0.013 mm is above deviations.hole.upper -1.013 mm"),
        }
        for name, (entry, field) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(fits.FitError, f"{field}.*accepted forms"):
                fits.declared_fit(entry)

    def test_an_iso_designation_may_separate_its_size_by_any_whitespace(self) -> None:
        for text in ("25\tH7/p6", "25  H7/p6", " 25 H7/p6 "):
            with self.subTest(repr(text)):
                self.assertEqual(fits.declared_fit({"fit": text})["type"], "interference")

    def test_a_vendor_designation_is_unclassified_where_the_kind_allows_one(self) -> None:
        self.assertEqual(fits.declared_fit({"fit": "5 dowel to vendor limits"}, vendor_designation=True)["type"],
                         "unclassified")
        with self.assertRaisesRegex(fits.FitError, "unknown key 'pairs'"):
            fits.declared_fit({"fit": "5 f7", "pairs": ["TST-0001", "TST-0002"]}, vendor_designation=True)
