# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from cadmodel import fillets, runner  # noqa: E402


class FilletFallbackTests(unittest.TestCase):
    class StdFail_NotDone(Exception):
        pass

    def test_successful_smaller_radius_is_returned_after_kernel_retry(self) -> None:
        shape = mock.Mock()
        edge = object()
        failed = self.StdFail_NotDone("too large")
        shape.fillet.side_effect = [failed, "small-radius-shape"]
        result = fillets.fillet_edge(shape, edge, 2.0)
        self.assertEqual(result, "small-radius-shape")
        self.assertEqual([call.args for call in shape.fillet.call_args_list], [(2.0, [edge]), (1.0, [edge])])

    def test_exhausted_cosmetic_edge_is_logged_and_leaves_shape_unchanged(self) -> None:
        shape = mock.Mock()
        shape.fillet.side_effect = self.StdFail_NotDone("cannot build")
        with self.assertLogs("cadmodel.fillets", level="WARNING") as captured:
            result = fillets.fillet_edge(shape, "rim", 1.0, retries=2, name="rim")
        self.assertIs(result, shape)
        self.assertEqual(shape.fillet.call_count, 3)
        self.assertIn("skipped non-functional fillet on rim", captured.output[0])

    def test_a_cosmetic_edge_filleted_smaller_reports_its_actual_radius(self) -> None:
        shape = mock.Mock()
        shape.fillet.side_effect = [self.StdFail_NotDone("too large"), self.StdFail_NotDone("too large"), "quarter"]
        with self.assertLogs("cadmodel.fillets", level="WARNING") as captured:
            self.assertEqual(fillets.fillet_edge(shape, "rim", 2.0, name="rim"), "quarter")
        self.assertEqual(len(captured.output), 1)
        self.assertIn("rim filleted at 0.5 mm, not the requested 2 mm", captured.output[0])

    def test_a_functional_edge_fails_at_its_specified_radius_and_never_shrinks(self) -> None:
        shape = mock.Mock()
        refused = self.StdFail_NotDone("too large")
        shape.fillet.side_effect = [refused, "half-radius-shape"]
        with self.assertRaises(self.StdFail_NotDone) as caught:
            fillets.fillet_edge(shape, "seat", 1.0, retries=2, functional=True)
        self.assertIs(caught.exception, refused)
        self.assertEqual([call.args for call in shape.fillet.call_args_list], [(1.0, ["seat"])])

    def test_a_functional_edge_shrinks_only_within_its_declared_range(self) -> None:
        within = mock.Mock()
        within.fillet.side_effect = [self.StdFail_NotDone("too large"), "half-radius-shape"]
        with self.assertLogs("cadmodel.fillets", level="WARNING") as captured:
            result = fillets.fillet_edge(within, "seat", 1.0, functional=True, min_radius=0.5, name="seat")
        self.assertEqual(result, "half-radius-shape")
        self.assertIn("seat filleted at 0.5 mm, not the requested 1 mm", captured.output[0])
        below = mock.Mock()
        below.fillet.side_effect = [self.StdFail_NotDone("too large"), "half-radius-shape"]
        with self.assertRaises(self.StdFail_NotDone):
            fillets.fillet_edge(below, "seat", 1.0, functional=True, min_radius=0.6)
        self.assertEqual([call.args for call in below.fillet.call_args_list], [(1.0, ["seat"])])

    def test_a_permitted_range_must_hold_the_requested_radius(self) -> None:
        for low in (0.0, -1.0, 2.0, float("nan")):
            with self.subTest(min_radius=low), self.assertRaisesRegex(ValueError, "min_radius"):
                fillets.fillet_edge(mock.Mock(), "seat", 1.0, functional=True, min_radius=low)

    def test_other_kernel_errors_are_not_treated_as_cosmetic_fallback(self) -> None:
        shape = mock.Mock()
        shape.fillet.side_effect = RuntimeError("bad API")
        with self.assertRaisesRegex(RuntimeError, "bad API"):
            fillets.fillet_edge(shape, "edge", 1.0)
        self.assertEqual(shape.fillet.call_count, 1)

    def test_model_warning_is_carried_in_runner_part_result(self) -> None:
        original = runner._build_part
        request = {"pn": "TST-0001", "model": "model.py"}

        def build_with_warning(_kernel, _request):
            logging.getLogger("cadmodel.fillets").warning("skipped edge")
            return {"ok": True}

        runner._build_part = build_with_warning
        try:
            self.assertEqual(runner.build_part("test", request),
                             {"ok": True, "warnings": ["skipped edge"]})
        finally:
            runner._build_part = original

if __name__ == "__main__":
    unittest.main()