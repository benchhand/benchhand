import sys
import unittest
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
import acceptance  # noqa: E402

RUBRIC = acceptance.load_rubric({"requirements": [{"id": "R-1", "text": "one"}, {"id": "R-2", "text": "two"},
                                                  {"id": "R-3", "text": "nice to have", "must": False}]})
PASSED = {"passed": True}


class VerdictTests(unittest.TestCase):
    def test_a_fenced_json_reply_gives_one_verdict_per_requirement(self) -> None:
        text = ('Here is my review.\n```json\n{"verdicts": [{"id": "R-1", "met": true, "reason": "shown"},'
                '{"id": "R-2", "met": false, "reason": "open"}, {"id": "R-3", "met": false}]}\n```')
        verdicts = acceptance.parse_verdicts(text, RUBRIC)
        self.assertEqual({key: value["met"] for key, value in verdicts.items()}, {"R-1": True, "R-2": False, "R-3": False})

    def test_a_reply_missing_a_requirement_or_with_a_non_boolean_verdict_is_rejected(self) -> None:
        for text in ('{"verdicts": [{"id": "R-1", "met": true}]}',
                     '{"verdicts": [{"id": "R-1", "met": "yes"}, {"id": "R-2", "met": true}, {"id": "R-3", "met": true}]}',
                     "no json here"):
            with self.subTest(text=text), self.assertRaises(acceptance.RubricError):
                acceptance.parse_verdicts(text, RUBRIC)

    def test_a_run_is_accepted_only_with_passing_hard_checks_and_every_must_have_met(self) -> None:
        met = {"R-1": {"met": True}, "R-2": {"met": True}, "R-3": {"met": False}}
        self.assertTrue(acceptance.accepted(PASSED, met, RUBRIC))
        self.assertFalse(acceptance.accepted({"passed": False}, met, RUBRIC))
        self.assertFalse(acceptance.accepted(PASSED, {**met, "R-2": {"met": False}}, RUBRIC))
        self.assertFalse(acceptance.accepted(PASSED, None, RUBRIC))


class HardCheckTests(unittest.TestCase):
    def test_the_newest_version_built_at_the_stage_is_chosen_by_number(self) -> None:
        versions = {"v2": "layout", "v9": "parts", "v10": "parts", "v11": "detail"}
        self.assertEqual(acceptance.latest_at_stage(versions, "parts"), "v10")
        self.assertIsNone(acceptance.latest_at_stage(versions, "analysis"))

    def test_unknown_process_coverage_is_not_fail_but_a_failed_packet_is(self) -> None:
        cases = [
            ({"status": "pass", "manufacturing": {"status": "unknown"}}, (True, True)),
            ({"status": "pass", "manufacturing": {"status": "fail"}}, (True, False)),
            ({"status": "fail", "manufacturing": {"status": "pass"}}, (False, True)),
            ({"status": "pass", "manufacturing": {"status": "not_assessed"}}, (True, False)),
            (None, (False, False)),
        ]
        for report, expected in cases:
            with self.subTest(report=report):
                results = acceptance.form_results(report, 1)
                self.assertEqual(tuple(result.passed for result in results), expected)

    def test_the_hard_verdict_fails_on_any_failed_check(self) -> None:
        ok, bad = acceptance.CheckResult("validate", True, ""), acceptance.CheckResult("sweep", False, "")
        self.assertTrue(acceptance.hard_verdict([ok])["passed"])
        self.assertEqual(acceptance.hard_verdict([ok, bad])["failed"], ["sweep"])


class CaseRubricTests(unittest.TestCase):
    def test_every_case_has_five_to_eight_must_have_requirements_and_mechanisms_need_the_sweep(self) -> None:
        sweeps = {}
        for folder in sorted(p for p in (BENCH / "cases").iterdir() if p.is_dir() and not p.name.startswith("_")):
            rubric = acceptance.load_rubric(yaml.safe_load((folder / "requirements.yaml").read_text(encoding="utf-8")))
            with self.subTest(case=folder.name):
                self.assertTrue(5 <= sum(r.must for r in rubric.requirements) <= 8)
            sweeps[folder.name] = rubric.sweep
        self.assertEqual(sweeps, {"fdm-enclosure": False, "lamp-arm": True, "print-in-place": True,
                                  "small-mechanism": True, "wall-bracket": False})


if __name__ == "__main__":
    unittest.main()
