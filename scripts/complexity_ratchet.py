#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["ruff==0.17.4"]
# ///
"""Fail when ruff complexity findings (C901, PLR0912, PLR0915) rise above the baseline."""

from __future__ import annotations

import argparse
import ast
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
# E902 is selected so unreadable files surface as diagnostics instead of silent warnings.
SELECTED = (*RULES, "E902")
IO_WARNING = re.compile(r"^warning: Failed to ", re.MULTILINE)

Finding = dict[str, Any]


class RatchetError(Exception):
    """ruff did not produce a trustworthy result; never treated as zero findings."""


def ruff_bin() -> str:
    from ruff.__main__ import find_ruff_bin

    return str(find_ruff_bin())


def _valid_diagnostic(item: object) -> bool:
    """Every field `collect` reads has the expected type."""
    if not isinstance(item, dict):
        return False
    location = item.get("location")
    return (
        isinstance(item.get("code"), str)
        and isinstance(item.get("filename"), str)
        and isinstance(item.get("message"), str)
        and isinstance(location, dict)
        and isinstance(location.get("row"), int)
        and not isinstance(location.get("row"), bool)
    )


def parse_ruff_output(returncode: int, stdout: str, stderr: str) -> list[Finding]:
    if returncode not in (0, 1):
        raise RatchetError(f"ruff exited {returncode}: {stderr.strip()}")
    if IO_WARNING.search(stderr):
        raise RatchetError(f"ruff skipped a file: {stderr.strip()}")
    if not stdout.strip():
        raise RatchetError("ruff produced no JSON output (expected at least `[]`)")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RatchetError(f"ruff output is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not all(_valid_diagnostic(item) for item in data):
        raise RatchetError("ruff JSON is not a list of well-formed diagnostics")
    if any(item["code"] == "E902" for item in data):
        raise RatchetError("ruff could not read a file (E902); the count would be understated")
    return data


def run_ruff() -> list[Finding]:
    cmd = [
        ruff_bin(), "check", "--isolated", "--no-cache", "--select", ",".join(SELECTED),
        "--output-format", "json", str(ROOT),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return parse_ruff_output(proc.returncode, proc.stdout, proc.stderr)


def enclosing_function(tree: ast.AST, row: int) -> str:
    """Dotted name (classes and functions) of the innermost def containing `row`."""
    best: list[str] = []

    def visit(node: ast.AST, path: list[str]) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = [*path, child.name]
                if child.lineno <= row <= (child.end_lineno or child.lineno):
                    if not isinstance(child, ast.ClassDef) and len(inner) >= len(best):
                        best = inner
                    visit(child, inner)
            else:
                visit(child, path)

    visit(tree, [])
    return ".".join(best) if best else "<module>"


def collect(findings: list[Finding], root: Path) -> Counter[str]:
    """Count findings keyed by `RULE path::function`, so moved code is identifiable."""
    keys: Counter[str] = Counter()
    trees: dict[Path, ast.AST | None] = {}
    for item in findings:
        file = Path(item["filename"])
        if file not in trees:
            try:
                trees[file] = ast.parse(file.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                trees[file] = None
        tree = trees[file]
        name = enclosing_function(tree, item["location"]["row"]) if tree else "<unresolved>"
        keys[f"{item['code']} {file.relative_to(root).as_posix()}::{name}"] += 1
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

    try:
        current = collect(run_ruff(), root)
    except RatchetError as exc:
        print(f"complexity ratchet cannot trust ruff: {exc}", file=sys.stderr)
        return 2
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
