#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["ruff==0.17.4"]
# ///
"""Fail when ruff complexity findings (C901, PLR0912, PLR0915) rise above the baseline."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("complexity-baseline.json")
RULES = ("C901", "PLR0912", "PLR0915")
FUNC = re.compile(r"`([^`]+)`")

Finding = dict[str, Any]


def run_ruff() -> list[Finding]:
    from ruff.__main__ import find_ruff_bin

    cmd = [
        find_ruff_bin(),
        "check",
        "--isolated",
        "--no-cache",
        "--select",
        ",".join(RULES),
        "--output-format",
        "json",
        str(ROOT),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        raise SystemExit(f"ruff failed: {proc.stderr.strip()}")
    result: list[Finding] = json.loads(proc.stdout or "[]")
    return result


def collect(findings: list[Finding], root: Path) -> Counter[str]:
    """Count findings keyed by `RULE path::function`, so moved code is identifiable."""
    keys: Counter[str] = Counter()
    for item in findings:
        match = FUNC.search(item.get("message", ""))
        name = match.group(1) if match else "?"
        path = Path(item["filename"]).relative_to(root).as_posix()
        keys[f"{item['code']} {path}::{name}"] += 1
    return keys


def totals(keys: Counter[str]) -> dict[str, int]:
    counts = dict.fromkeys(RULES, 0)
    for key, n in keys.items():
        counts[key.split(" ", 1)[0]] += n
    return counts


def make_baseline(keys: Counter[str]) -> dict[str, Any]:
    return {"counts": totals(keys), "offenders": sorted(keys.elements())}


def load_baseline(path: Path) -> Counter[str]:
    if not path.exists():
        return Counter()
    data = json.loads(path.read_text())
    return Counter(data["offenders"])


def main(
    argv: list[str] | None = None,
    run_ruff: Callable[[], list[Finding]] = run_ruff,
    root: Path = ROOT,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--update", action="store_true", help="rewrite the baseline (downward only)"
    )
    parser.add_argument(
        "--allow-increase", action="store_true", help="let --update raise counts"
    )
    args = parser.parse_args(argv)

    current = collect(run_ruff(), root)
    now = totals(current)
    base = totals(load_baseline(args.baseline))
    risen = [rule for rule in RULES if now[rule] > base[rule]]
    summary = " / ".join(
        f"{rule} {now[rule]} (baseline {base[rule]})" for rule in RULES
    )

    if args.update:
        if risen and not args.allow_increase:
            print(
                f"Refusing to raise the baseline ({summary}); pass --allow-increase to force."
            )
            return 1
        args.baseline.write_text(json.dumps(make_baseline(current), indent=1) + "\n")
        print(f"Baseline written: {summary}")
        return 0

    if risen:
        new = sorted((current - load_baseline(args.baseline)).elements())
        print(f"Complexity rose: {summary}")
        print("New offenders (not in baseline by rule, file and function):")
        for key in new:
            print(f"  {key}")
        return 1
    print(f"Complexity ok: {summary}")
    if any(now[rule] < base[rule] for rule in RULES):
        print("Counts dropped: lock the gain with `complexity_ratchet.py --update`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
