import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import report  # noqa: E402


def record(case: str, arm: str, spend: float = 1.0, accepted: bool = False, **values: float) -> dict:
    return {"case": case, "arm": arm, "mode": "bench", "accepted": accepted, "metrics": values,
            "spend": {"total_usd": spend}}


class DescriptiveTests(unittest.TestCase):
    def test_quantiles_interpolate_between_order_statistics(self) -> None:
        self.assertEqual(report.quantile([4, 1, 3, 2], 0.5), 2.5)
        self.assertAlmostEqual(report.quantile([1, 2, 3, 4], 0.9), 3.7)

    def test_cv_is_sample_stdev_over_mean_and_undefined_for_one_value(self) -> None:
        self.assertAlmostEqual(report.cv([2, 4]), math.sqrt(2) / 3)
        self.assertIsNone(report.cv([5]))

    def test_summary_groups_by_case_and_reports_acceptance_per_dollar(self) -> None:
        records = [record("a", "x", 2.0, True, model_turns=10), record("a", "x", 2.0, False, model_turns=20),
                   record("b", "x", 4.0, True, model_turns=30)]
        result = report.summary(records, ("model_turns",))
        self.assertEqual(result["by_case"]["a"]["metrics"]["model_turns"]["p50"], 15)
        self.assertEqual(result["overall"]["acceptance"],
                         {"runs": 3, "accepted": 2, "acceptance_rate": 2 / 3, "spend_usd": 8.0, "accepted_per_usd": 0.25})


class CompareTests(unittest.TestCase):
    def test_a_constant_doubling_in_every_case_has_ratio_two_and_a_point_interval(self) -> None:
        records = [record(case, "a", tool_calls=v) for case, v in (("p", 10), ("p", 10), ("q", 50), ("q", 50))]
        records += [record(case, "b", tool_calls=2 * v) for case, v in (("p", 10), ("p", 10), ("q", 50), ("q", 50))]
        result = report.compare(records, "tool_calls", "a", "b", resamples=200)
        self.assertAlmostEqual(result["overall"]["ratio"], 2.0)
        self.assertAlmostEqual(result["overall"]["ci_low"], 2.0)
        self.assertAlmostEqual(result["overall"]["ci_high"], 2.0)
        self.assertEqual(set(result["cases"]), {"p", "q"})

    def test_a_metric_with_zeros_is_compared_on_log_of_value_plus_one(self) -> None:
        records = [record("p", "a", failed_builds=0), record("p", "b", failed_builds=1)]
        result = report.compare(records, "failed_builds", "a", "b", resamples=50)
        self.assertEqual(result["offset"], 1.0)
        self.assertAlmostEqual(result["cases"]["p"]["ratio"], 2.0)

    def test_the_interval_brackets_the_point_estimate_when_runs_vary(self) -> None:
        records = [record("p", "a", spend_usd=v) for v in (8, 10, 12)] + [record("p", "b", spend_usd=v) for v in (5, 6, 7)]
        overall = report.compare(records, "spend_usd", "a", "b", resamples=2000)["overall"]
        self.assertLess(overall["ci_low"], overall["ratio"])
        self.assertGreater(overall["ci_high"], overall["ratio"])
        self.assertLess(overall["ci_high"], 1.0)


class SizeTests(unittest.TestCase):
    def test_repeats_for_a_20_percent_change_follow_the_normal_approximation(self) -> None:
        # z(0.975) + z(0.8) = 2.80158; 2 * (2.80158 * 0.3 / ln 1.2)^2 / 5 cases = 8.50, so 9 repeats.
        self.assertEqual(report.repeats_needed(0.3, 0.2, 5), 9)

    def test_the_detectable_effect_inverts_the_repeats(self) -> None:
        # 2.80158 * 0.3 * sqrt(2 / 45) = 0.17719, exp(0.17719) - 1 = 0.1939.
        self.assertAlmostEqual(report.detectable_effect(0.3, 9, 5), 0.1939, places=3)

    def test_a_plan_over_budget_reports_the_largest_affordable_design(self) -> None:
        records = [record(case, "base", 10.0, spend_usd=v) for case in "abcde" for v in (6.0, 10.0, 14.0)]
        plan = report.size_plan(records, ("spend_usd",), effect=0.2, cases=5, arms=2, budget=300.0)
        self.assertGreater(plan["repeats"], 3)
        self.assertFalse(plan["fits"])
        self.assertEqual(plan["affordable_repeats"], 3)  # 300 // (10 USD x 5 cases x 2 arms)
        self.assertGreater(plan["affordable_detectable_effect"]["spend_usd"], 0.2)


class LedgerTests(unittest.TestCase):
    def test_the_ledger_total_sums_every_entry_by_phase(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            lines = [{"phase": "fixtures", "total_usd": 12.5}, {"phase": "fixtures", "total_usd": 2.5},
                     {"phase": "pilot", "total_usd": 5}]
            (Path(folder) / "spend.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
            self.assertEqual(report.ledger_total(Path(folder)),
                             {"entries": 3, "total_usd": 20.0, "by_phase": {"fixtures": 15.0, "pilot": 5.0}})


if __name__ == "__main__":
    unittest.main()
