#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Reproducibility guard: two builds of the same record must seal byte-identical files.

Two modes:
    repro_check.py                      build examples/lamp-arm (migrated to record version 2) twice into two
                                        scratch projects, then compare their hw/versions trees
    repro_check.py --compare A B        compare two already-built hw directories (for example before and after
                                        a change to how parts are built)

Every file under hw/versions/<label> is compared byte for byte. Nothing is normalised silently: a field that is
legitimately volatile is listed in VOLATILE with the reason, masked for the comparison only, and reported on every
run in which it differed. Exit status: 0 identical (allowlisted fields apart), 1 mismatch, 2 usage or build failure.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXAMPLE_HW = ROOT / "examples" / "lamp-arm" / "hw"
CAD_MODEL = SKILLS / "cad-model" / "scripts" / "cad_model.py"
MIGRATE = SKILLS / "cad-record" / "scripts" / "migrate.py"
SEALED = "versions"  # the sealed output of a build, relative to hw/


@dataclass(frozen=True)
class Volatile:
    """One field allowed to differ between two builds. `kind` is "json-key" (every JSON member whose
    "<parent key>.<key>" matches the regex `target` is dropped before comparing; the parent of a top-level member is
    empty) or "line-regex" (lines matching the regex are dropped)."""

    glob: str  # matched against the path relative to hw/, with fnmatch
    kind: str
    target: str
    reason: str


# Each entry is a known bug or a defended exception; the PR body lists them. Keep the reason current and delete
# an entry when its cause is fixed. Empty means the sealed output is fully reproducible.
_STEP_HASHED = r"^files\.(.*/)?(manufacturing\.json|[^/]*\.step)$"
VOLATILE: tuple[Volatile, ...] = (
    Volatile(
        "versions/*.step", "line-regex", r"^FILE_NAME\(",
        "STEP FILE_NAME header carries the export wall-clock time; fix is in cad-motion pose.py (package A)",
    ),
    Volatile(
        "versions/*/manifest.json", "json-key", r"^\.created_utc$",
        "build wall-clock stamp, by design; the manifest is not a content address",
    ),
    Volatile(
        "versions/*/manifest.json", "json-key", r"\.import_s$",
        "tools.<kernel>.import_s is the measured kernel import time",
    ),
    Volatile(
        "versions/*/manifest.json", "json-key", _STEP_HASHED,
        "hashes of STEP files and of manufacturing.json files, which change only through the STEP header timestamp",
    ),
    Volatile(
        "versions/*/manifest.json", "json-key", r"^installed\.sha256$",
        "hash of the installed part.step, which changes only through the STEP header timestamp",
    ),
    Volatile(
        "versions/*/manufacturing/*/manufacturing.json", "json-key", r"^files\.part\.step$|^installed\.sha256$",
        "hash of part.step, which changes only through the STEP header timestamp",
    ),
)


@dataclass
class Mismatch:
    path: str
    detail: str


@dataclass
class Used:
    path: str
    rule: Volatile


@dataclass
class Report:
    compared: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    allowlisted: list[Used] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _drop_key(node: Any, pattern: re.Pattern[str], removed: list[Any], parent: str = "") -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if pattern.search(f"{parent}.{k}"):
                removed.append(v)
            else:
                out[k] = _drop_key(v, pattern, removed, k)
        return out
    if isinstance(node, list):
        return [_drop_key(v, pattern, removed, parent) for v in node]
    return node


def _apply(rule: Volatile, data: bytes) -> tuple[bytes, list[Any]] | None:
    """The data with the rule's volatile part dropped, and what was dropped; None when the rule does not apply."""
    removed: list[Any] = []
    if rule.kind == "json-key":
        try:
            doc = json.loads(data)
        except ValueError:
            return None
        masked = json.dumps(
            _drop_key(doc, re.compile(rule.target), removed), sort_keys=True
        ).encode()
        return masked, removed
    if rule.kind == "line-regex":
        pattern = re.compile(rule.target)
        kept: list[str] = []
        for line in data.decode("latin-1").splitlines(keepends=True):
            (removed if pattern.search(line.rstrip("\r\n")) else kept).append(line)
        return "".join(kept).encode("latin-1"), removed
    raise ValueError(f"unknown allowlist kind {rule.kind!r}")


def _json_paths_differing(a: Any, b: Any, prefix: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for key in sorted(set(a) | set(b)):
            out += _json_paths_differing(a.get(key), b.get(key), f"{prefix}.{key}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [
            p
            for i, (x, y) in enumerate(zip(a, b))
            for p in _json_paths_differing(x, y, f"{prefix}[{i}]")
        ]
    return [] if a == b else [prefix]


def _describe(a: bytes, b: bytes) -> str:
    try:
        paths = _json_paths_differing(json.loads(a), json.loads(b))
    except ValueError:
        paths = []
    if paths:
        shown = ", ".join(paths[:5]) + (
            f" (+{len(paths) - 5} more)" if len(paths) > 5 else ""
        )
        return f"JSON differs at {shown}"
    offset = next(
        (i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b))
    )
    size = f"; sizes {len(a)} vs {len(b)}" if len(a) != len(b) else ""
    return f"first differing byte at offset {offset}{size}"


def _files(hw: Path) -> dict[str, Path]:
    base = hw / SEALED
    return {
        p.relative_to(hw).as_posix(): p for p in sorted(base.rglob("*")) if p.is_file()
    }


def compare(hw_a: Path, hw_b: Path, allow: tuple[Volatile, ...] = VOLATILE) -> Report:
    """Compare every file under hw/versions of the two trees byte for byte, minus the allowlisted fields."""
    files_a, files_b = _files(hw_a), _files(hw_b)
    report = Report()
    for rel in sorted(set(files_a) | set(files_b)):
        if rel not in files_b or rel not in files_a:
            report.mismatches.append(
                Mismatch(rel, "missing in B" if rel not in files_b else "missing in A")
            )
            continue
        report.compared += 1
        a, b = files_a[rel].read_bytes(), files_b[rel].read_bytes()
        if a == b:
            continue
        used: list[Used] = []
        for rule in (r for r in allow if fnmatch.fnmatch(rel, r.glob)):
            masked_a, masked_b = _apply(rule, a), _apply(rule, b)
            if masked_a is None or masked_b is None:
                continue
            if masked_a[1] != masked_b[1]:
                used.append(Used(rel, rule))
            a, b = masked_a[0], masked_b[0]
        if a == b:
            report.allowlisted += used
        else:
            report.mismatches.append(Mismatch(rel, _describe(a, b)))
    return report


def render(report: Report, label_a: str, label_b: str) -> str:
    lines = [
        f"A = {label_a}",
        f"B = {label_b}",
        f"{report.compared} sealed files present in both",
    ]
    for used in report.allowlisted:
        lines.append(
            f"  allowlisted  {used.path}: {used.rule.kind} {used.rule.target!r} ({used.rule.reason})"
        )
    for miss in report.mismatches:
        lines.append(f"  DIFFERS      {miss.path}: {miss.detail}")
    lines.append(
        "IDENTICAL"
        if report.ok
        else f"MISMATCH: {len(report.mismatches)} file(s) differ"
    )
    return "\n".join(lines)


def _run(*command: str) -> None:
    proc = subprocess.run(
        command, capture_output=True, text=True, errors="replace", check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command[:4])} failed ({proc.returncode}): {proc.stderr[-2000:]}"
        )


def build_twice(scratch: Path, label: str, kernel: str) -> tuple[Path, Path]:
    """Migrate the example once, copy it to two projects, and build `label` in each."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required on PATH")
    seed = scratch / "seed" / "hw"
    shutil.copytree(EXAMPLE_HW, seed)
    _run(uv, "run", "--script", str(MIGRATE), str(seed), "--apply")
    hws = []
    for name in ("build-a", "build-b"):
        hw = scratch / name / "hw"
        shutil.copytree(seed, hw)
        print(f"building {name} ...", file=sys.stderr, flush=True)
        _run(
            uv,
            "run",
            "--script",
            str(CAD_MODEL),
            "run",
            str(hw),
            "--label",
            label,
            "--kernel",
            kernel,
        )
        hws.append(hw)
    return hws[0], hws[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("A", "B"),
        type=Path,
        help="compare two built hw directories",
    )
    parser.add_argument(
        "--label", default="v1", help="version label to build (build mode)"
    )
    parser.add_argument(
        "--kernel", default="auto", choices=("auto", "cadquery", "build123d")
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the scratch projects (build mode)"
    )
    args = parser.parse_args(argv)
    if args.compare:
        a, b = (p.resolve() for p in args.compare)
        for hw in (a, b):
            if not (hw / SEALED).is_dir():
                print(f"repro_check: {hw} has no {SEALED}/ directory", file=sys.stderr)
                return 2
        report = compare(a, b)
        print(render(report, str(a), str(b)))
        return 0 if report.ok else 1
    scratch = Path(tempfile.mkdtemp(prefix="cad-repro-"))
    try:
        a, b = build_twice(scratch, args.label, args.kernel)
        report = compare(a, b)
        print(render(report, str(a), str(b)))
        return 0 if report.ok else 1
    except RuntimeError as error:
        print(f"repro_check: {error}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"scratch kept at {scratch}", file=sys.stderr)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
