#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Developer tool: byte-compare the sealed output of two builds of the same record.

    repro_check.py --compare A B

A and B are hw directories. Every file under hw/versions of both is compared byte for byte, nothing normalised.
Use it to check that a refactor (parallel part builds, a new cache) leaves the output unchanged: build once
before and once after the change, then compare. Prints one line per differing file, with the first differing
byte or the JSON paths that differ. Exit status: 0 identical, 1 mismatch, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEALED = "versions"  # the sealed output of a build, relative to hw/


@dataclass
class Mismatch:
    path: str
    detail: str


@dataclass
class Report:
    compared: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _json_paths_differing(a: Any, b: Any, prefix: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for key in sorted(set(a) | set(b)):
            out += _json_paths_differing(a.get(key), b.get(key), f"{prefix}.{key}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _json_paths_differing(x, y, f"{prefix}[{i}]")]
    return [] if a == b else [prefix]


def _describe(a: bytes, b: bytes) -> str:
    try:
        paths = _json_paths_differing(json.loads(a), json.loads(b))
    except ValueError:
        paths = []
    if paths:
        shown = ", ".join(paths[:5]) + (f" (+{len(paths) - 5} more)" if len(paths) > 5 else "")
        return f"JSON differs at {shown}"
    offset = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    size = f"; sizes {len(a)} vs {len(b)}" if len(a) != len(b) else ""
    return f"first differing byte at offset {offset}{size}"


def _files(hw: Path) -> dict[str, Path]:
    return {p.relative_to(hw).as_posix(): p for p in sorted((hw / SEALED).rglob("*")) if p.is_file()}


def compare(hw_a: Path, hw_b: Path) -> Report:
    """Compare every file under hw/versions of the two trees byte for byte."""
    files_a, files_b = _files(hw_a), _files(hw_b)
    report = Report()
    for rel in sorted(set(files_a) | set(files_b)):
        if rel not in files_a or rel not in files_b:
            report.mismatches.append(Mismatch(rel, "missing in A" if rel not in files_a else "missing in B"))
            continue
        report.compared += 1
        a, b = files_a[rel].read_bytes(), files_b[rel].read_bytes()
        if a != b:
            report.mismatches.append(Mismatch(rel, _describe(a, b)))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--compare", nargs=2, required=True, metavar=("A", "B"), type=Path, help="two built hw directories")
    a, b = (p.resolve() for p in parser.parse_args(argv).compare)
    for hw in (a, b):
        if not (hw / SEALED).is_dir():
            print(f"repro_check: {hw} has no {SEALED}/ directory", file=sys.stderr)
            return 2
    report = compare(a, b)
    print(f"A = {a}\nB = {b}\n{report.compared} sealed files present in both")
    for miss in report.mismatches:
        print(f"  DIFFERS  {miss.path}: {miss.detail}")
    print("IDENTICAL" if report.ok else f"MISMATCH: {len(report.mismatches)} file(s) differ")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
