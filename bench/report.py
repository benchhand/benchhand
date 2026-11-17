#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Statistics over agent-throughput benchmark run records.

Reads `runs/*/record.json` under a results directory (the bench/results branch checkout).

- `summary`: p50, p90, mean and CV of every metric, per case and overall, plus the acceptance
  rate and accepted designs per dollar.
- `compare --a ARM --b ARM`: per-case log ratios b/a of each metric, matched by case, with
  percentile bootstrap confidence intervals, and their mean over cases.
- `size`: repeats per case and arm to detect a given change on matched log metrics, and the cost.
- `spend`: the running spend total of the ledger.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

JSON = dict[str, Any]

METRICS = (
    "model_turns", "lead_model_turns", "tool_calls", "input_tokens", "cached_input_tokens", "output_tokens",
    "spend_usd", "subagent_dispatches", "subagent_redispatches", "failed_builds", "active_wall_s",
    "human_wait_events", "human_wait_s", "questions_asked", "questions_unmatched",
)
CONFIDENCE = 0.95


def quantile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (the 'inclusive' or type 7 definition)."""
    if not values:
        raise ValueError("quantile of no values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def cv(values: Sequence[float]) -> float | None:
    """Sample coefficient of variation; None with fewer than two values or a zero mean."""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return None if mean == 0 else statistics.stdev(values) / mean


def describe(values: Sequence[float]) -> JSON:
    spread = cv(values)
    return {"n": len(values), "p50": quantile(values, 0.5), "p90": quantile(values, 0.9),
            "mean": statistics.fmean(values), "cv": None if spread is None else round(spread, 4)}


def load_records(results: Path, mode: str | None = "bench") -> list[JSON]:
    records = []
    for path in sorted(results.glob("runs/*/record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if mode is None or record.get("mode") == mode:
            records.append(record)
    return records


def metric_value(record: Mapping[str, Any], metric: str) -> float | None:
    value = (record.get("metrics") or {}).get(metric)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _values(records: Iterable[Mapping[str, Any]], metric: str) -> list[float]:
    return [value for record in records if (value := metric_value(record, metric)) is not None]


def _group(records: Iterable[JSON], key: Callable[[JSON], str]) -> dict[str, list[JSON]]:
    groups: dict[str, list[JSON]] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return dict(sorted(groups.items()))


def run_spend(record: Mapping[str, Any]) -> float:
    return float((record.get("spend") or {}).get("total_usd") or 0.0)


def acceptance(records: Sequence[Mapping[str, Any]]) -> JSON:
    accepted = sum(1 for record in records if record.get("accepted") is True)
    spend = sum(run_spend(record) for record in records)
    return {"runs": len(records), "accepted": accepted,
            "acceptance_rate": accepted / len(records) if records else None,
            "spend_usd": round(spend, 4), "accepted_per_usd": accepted / spend if spend else None}


def summary(records: Sequence[JSON], metrics: Sequence[str] = METRICS) -> JSON:
    def block(group: Sequence[JSON]) -> JSON:
        stats = {metric: describe(values) for metric in metrics if (values := _values(group, metric))}
        return {"metrics": stats, "acceptance": acceptance(group)}

    return {"overall": block(records),
            "by_case": {case: block(group) for case, group in _group(records, lambda r: str(r.get("case"))).items()}}


def _offset(values: Iterable[float]) -> float:
    """log(x + offset): 1 when any value is zero or negative (counts such as failed builds), else 0."""
    return 1.0 if any(value <= 0 for value in values) else 0.0


def _mean_log(values: Sequence[float], offset: float) -> float:
    return statistics.fmean(math.log(value + offset) for value in values)


def _percentile_ci(samples: list[float]) -> tuple[float, float]:
    tail = (1 - CONFIDENCE) / 2
    return quantile(samples, tail), quantile(samples, 1 - tail)


def compare(records: Sequence[JSON], metric: str, arm_a: str, arm_b: str,
            resamples: int = 10000, seed: int = 0) -> JSON:
    """Per-case log ratio of geometric means b/a, matched by case, with percentile bootstrap CIs."""
    cases: dict[str, tuple[list[float], list[float]]] = {}
    for case, group in _group(records, lambda r: str(r.get("case"))).items():
        a = _values([r for r in group if r.get("arm") == arm_a], metric)
        b = _values([r for r in group if r.get("arm") == arm_b], metric)
        if a and b:
            cases[case] = (a, b)
    if not cases:
        return {"metric": metric, "cases": {}, "overall": None}
    offset = _offset(value for a, b in cases.values() for value in (*a, *b))
    rng = random.Random(seed)
    boot: dict[str, list[float]] = {case: [] for case in cases}
    overall: list[float] = []
    for _ in range(resamples):
        diffs = []
        for case, (a, b) in cases.items():
            diff = _mean_log(rng.choices(b, k=len(b)), offset) - _mean_log(rng.choices(a, k=len(a)), offset)
            boot[case].append(diff)
            diffs.append(diff)
        overall.append(statistics.fmean(diffs))

    def entry(point: float, samples: list[float], extra: JSON) -> JSON:
        low, high = _percentile_ci(samples)
        return {**extra, "log_ratio": point, "ratio": math.exp(point), "ci_low": math.exp(low), "ci_high": math.exp(high)}

    points = {case: _mean_log(b, offset) - _mean_log(a, offset) for case, (a, b) in cases.items()}
    return {
        "metric": metric, "offset": offset, "confidence": CONFIDENCE, "resamples": resamples,
        "cases": {case: entry(points[case], boot[case], {"n_a": len(a), "n_b": len(b)})
                  for case, (a, b) in cases.items()},
        "overall": entry(statistics.fmean(points.values()), overall, {"cases": len(cases)}),
    }


def within_case_log_sd(records: Sequence[JSON], metric: str) -> float | None:
    """Pooled within-case standard deviation of log(metric + offset)."""
    groups = [_values(group, metric) for group in _group(records, lambda r: str(r.get("case"))).values()]
    groups = [values for values in groups if len(values) >= 2]
    if not groups:
        return None
    offset = _offset(value for values in groups for value in values)
    pooled = sum((len(v) - 1) * statistics.variance([math.log(x + offset) for x in v]) for v in groups)
    return math.sqrt(pooled / sum(len(v) - 1 for v in groups))


def _z(alpha: float, power: float) -> float:
    normal = statistics.NormalDist()
    return normal.inv_cdf(1 - alpha / 2) + normal.inv_cdf(power)


def repeats_needed(sigma: float, effect: float, cases: int, alpha: float = 0.05, power: float = 0.8) -> int:
    """Repeats per case and arm for a two-sided test of the case-matched mean log ratio.

    The estimate averages `cases` per-case differences of two means of `n` runs each, so its
    variance is 2 sigma^2 / (cases n); a change of `effect` is a log difference of ln(1 + effect).
    """
    delta = math.log1p(effect)
    return max(2, math.ceil(2 * (_z(alpha, power) * sigma / delta) ** 2 / cases))


def detectable_effect(sigma: float, repeats: int, cases: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """The smallest relative change `repeats` per case and arm detect: the inverse of repeats_needed."""
    return math.expm1(_z(alpha, power) * sigma * math.sqrt(2 / (cases * repeats)))


def size_plan(records: Sequence[JSON], metrics: Sequence[str], effect: float, cases: int, arms: int,
              budget: float | None, alpha: float = 0.05, power: float = 0.8) -> JSON:
    """Repeats for each metric, the design that covers them all, its cost, and the largest design the budget fits."""
    per_metric: JSON = {}
    for metric in metrics:
        sigma = within_case_log_sd(records, metric)
        if sigma is not None and sigma > 0:
            per_metric[metric] = {"log_sd": round(sigma, 4), "repeats": repeats_needed(sigma, effect, cases, alpha, power)}
    cost = statistics.fmean([run_spend(r) for r in records]) if records else 0.0
    needed = max((entry["repeats"] for entry in per_metric.values()), default=0)
    plan: JSON = {"effect": effect, "alpha": alpha, "power": power, "cases": cases, "arms": arms,
                  "cost_per_run_usd": round(cost, 4), "per_metric": per_metric, "repeats": needed,
                  "runs": needed * cases * arms, "cost_usd": round(needed * cases * arms * cost, 2)}
    if budget is not None and cost > 0:
        affordable = int(budget // (cost * cases * arms))
        plan["budget_usd"] = budget
        plan["fits"] = plan["cost_usd"] <= budget
        plan["affordable_repeats"] = affordable
        if affordable >= 2:
            plan["affordable_detectable_effect"] = {
                metric: round(detectable_effect(entry["log_sd"], affordable, cases, alpha, power), 4)
                for metric, entry in per_metric.items()}
    return plan


def ledger_total(results: Path) -> JSON:
    entries = []
    ledger = results / "spend.jsonl"
    if ledger.exists():
        entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_phase: dict[str, float] = {}
    for entry in entries:
        phase = str(entry.get("phase", "?"))
        by_phase[phase] = round(by_phase.get(phase, 0.0) + float(entry.get("total_usd") or 0.0), 4)
    return {"entries": len(entries), "total_usd": round(sum(by_phase.values()), 4), "by_phase": by_phase}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("summary", "compare", "size", "spend"):
        command = sub.add_parser(name)
        command.add_argument("results", type=Path, help="results directory (bench/results checkout)")
        if name != "spend":
            command.add_argument("--mode", default="bench", help="run mode to include (default bench)")
            command.add_argument("--arm", action="append", help="only these arms (summary, size)")
            command.add_argument("--metric", action="append", help="metrics to report (default all)")
    compare_parser = sub.choices["compare"]
    compare_parser.add_argument("--a", required=True, help="baseline arm")
    compare_parser.add_argument("--b", required=True, help="treatment arm")
    compare_parser.add_argument("--resamples", type=int, default=10000)
    size_parser = sub.choices["size"]
    size_parser.add_argument("--effect", type=float, default=0.2)
    size_parser.add_argument("--alpha", type=float, default=0.05)
    size_parser.add_argument("--power", type=float, default=0.8)
    size_parser.add_argument("--cases", type=int, default=5)
    size_parser.add_argument("--arms", type=int, default=2)
    size_parser.add_argument("--budget", type=float, help="USD left for the design")
    args = parser.parse_args(argv)
    if args.command == "spend":
        print(json.dumps(ledger_total(args.results), indent=2))
        return 0
    records = load_records(args.results, args.mode)
    if args.arm and args.command != "compare":
        records = [record for record in records if record.get("arm") in args.arm]
    metrics = tuple(args.metric or METRICS)
    if args.command == "summary":
        result: Any = summary(records, metrics)
    elif args.command == "compare":
        result = [compare(records, metric, args.a, args.b, args.resamples) for metric in metrics]
    else:
        result = size_plan(records, metrics, args.effect, args.cases, args.arms, args.budget, args.alpha, args.power)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
