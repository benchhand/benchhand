"""Mutation corpus core: pairs, families, the registry and the rate arithmetic.

A Pair is one defect with its clean twin. `run(mutant)` builds the case, runs one manufacturability or
constructability check and returns an outcome; the same function builds both variants, so the twin differs
from the mutant only by the defect. Families live in `families/*.py` and register by exposing `FAMILY`.
Pure standard library: the check and its fixtures are imported lazily inside `run`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Outcome = Literal["fail", "pass", "unknown"]
OUTCOMES: tuple[str, ...] = ("fail", "pass", "unknown")
HERE = Path(__file__).resolve().parent
FAMILIES_DIR = HERE / "families"
FIXTURES_DIR = HERE / "fixtures"


class Skip(Exception):
    """Raised by `run` when the case cannot run here (a missing tool, say); it is excluded from the rates."""


@dataclass(frozen=True)
class Pair:
    id: str  # unique within the family
    defect: str  # the one thing the mutant gets wrong, in a phrase
    run: Callable[[bool], Outcome]  # run(True) is the must-FAIL mutant, run(False) its must-PASS twin


@dataclass(frozen=True)
class Family:
    id: str  # unique, snake_case: <check>_<defect class>
    check: str  # the check under test, as a module path or script name
    pairs: tuple[Pair, ...]
    owner: str = ""  # the package or area that maintains it
    notes: str = field(default="", compare=False)


@dataclass
class PairResult:
    id: str
    defect: str
    mutant: str | None = None
    twin: str | None = None
    skipped: str | None = None
    error: str | None = None


def load_families(directory: Path = FAMILIES_DIR) -> list[Family]:
    """Import every `families/*.py` (not starting with `_`) and collect its `FAMILY` (or `FAMILIES`)."""
    found: list[Family] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = f"mfg_corpus_family_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        found.extend(getattr(module, "FAMILIES", None) or [module.FAMILY])
    validate(found)
    return found


def validate(families: Iterable[Family]) -> None:
    seen: set[str] = set()
    for family in families:
        if family.id in seen:
            raise ValueError(f"duplicate family id {family.id}")
        seen.add(family.id)
        ids = [pair.id for pair in family.pairs]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"family {family.id} needs unique, non-empty pair ids")


def run_pair(pair: Pair) -> PairResult:
    result = PairResult(pair.id, pair.defect)
    try:
        result.mutant = pair.run(True)
        result.twin = pair.run(False)
    except Skip as skip:
        result.mutant = result.twin = None
        result.skipped = str(skip) or "skipped"
        return result
    except Exception as exc:  # a crashing check is recorded, never hidden as a pass
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    for variant in (result.mutant, result.twin):
        if variant not in OUTCOMES:
            result.error = f"outcome {variant!r} is not one of {OUTCOMES}"
    return result


def rate(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def summarise(family: Family, results: list[PairResult]) -> dict[str, Any]:
    """Rates over the pairs that ran: detection = FAIL on mutant, abstention = UNKNOWN on mutant,
    false positive = FAIL on twin. A pair that errored counts as a miss on the mutant and no verdict on the twin."""
    ran = [r for r in results if not r.skipped]
    n = len(ran)
    detected = sum(r.mutant == "fail" for r in ran)
    abstained = sum(r.mutant == "unknown" for r in ran)
    missed = sum(r.mutant == "pass" for r in ran)
    false_pos = sum(r.twin == "fail" for r in ran)
    return {
        "family": family.id, "check": family.check, "owner": family.owner,
        "pairs": len(results), "ran": n, "skipped": len(results) - n, "errors": sum(bool(r.error) for r in ran),
        "detected": detected, "abstained": abstained, "missed": missed, "false_positives": false_pos,
        "detection_rate": rate(detected, n), "abstention_rate": rate(abstained, n),
        "false_positive_rate": rate(false_pos, n),
        "results": [vars(r) for r in results],
    }


def evaluate(families: Iterable[Family]) -> list[dict[str, Any]]:
    return [summarise(f, [run_pair(p) for p in f.pairs]) for f in families]


def table(rows: list[dict[str, Any]]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.0f}%"

    head = ("family", "ran/pairs", "detect", "abstain", "false+")
    body = [(r["family"], f"{r['ran']}/{r['pairs']}", pct(r["detection_rate"]), pct(r["abstention_rate"]),
             pct(r["false_positive_rate"])) for r in rows]
    widths = [max(len(str(x[i])) for x in (head, *body)) for i in range(len(head))]
    lines = ["  ".join(str(x[i]).ljust(widths[i]) for i in range(len(head))).rstrip() for x in (head, *body)]
    return "\n".join(lines)
