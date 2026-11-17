"""install_missing: an assembly occurrence that no operation installs, other than its one base.

cad-record assembly_operations.check_order treats every occurrence no operation installs as base (present
from the start). A part whose installing operation was lost then looks like a second base part and the
sequence passes, whether nothing else names it or it only receives or is closed over. The mutant drops one
installing operation from the housing assembly sequence; the twin keeps it. Both variants are built by
`_outcome`.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from corpus import Family, Pair, Skip

ROOT = Path(__file__).resolve().parents[5]
RECORD_SCRIPTS = ROOT / "skills" / "cad-record" / "scripts"
PNS = ("TST-0101", "TST-0102", "TST-0103", "TST-0104", "TST-0105")  # housing, bearing, cover, screw, bracket
DOWN = [0, 0, -1]

# The housing assembly sequence of cad-motion's blocked_socket fixture, without tool envelopes.
OPERATIONS: list[dict[str, Any]] = [
    {"id": "seat-bearing", "kind": "press_fit", "part": "TST-0102", "into": "TST-0101", "interface": "IF-001",
     "direction": DOWN, "acceptance": {"kind": "seated", "features": ["outer_face", "seat_floor"]}},
    {"id": "fit-cover", "kind": "close", "part": "TST-0103", "into": "TST-0101", "closes": ["TST-0102"],
     "direction": DOWN},
    {"id": "mount-bracket", "kind": "join", "part": "TST-0105", "into": "TST-0101", "direction": DOWN,
     "acceptance": {"kind": "standoff", "features": ["bracket_foot", "housing_top"]}},
    {"id": "fasten-cover", "kind": "fasten", "part": "TST-0104", "into": "TST-0103", "direction": DOWN,
     "acceptance": {"kind": "seated", "features": ["head_face", "cover_top"]}},
]


def _outcome(dropped: str | None) -> str:
    """check_order over the sequence without the operation `dropped`: "fail" when any order outcome fails."""
    try:
        if str(ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "scripts"))
        from modload import load_module

        operations = load_module("cad_record_assembly_operations", RECORD_SCRIPTS / "assembly_operations.py")
        data = load_module("cad_record_assembly_data", RECORD_SCRIPTS / "assembly_data.py")
    except ImportError as error:
        raise Skip(f"cad-record scripts not importable: {error}") from error
    items = [copy.deepcopy(op) for op in OPERATIONS if op["id"] != dropped]
    inventory = data.normalize({"subassemblies": [{"id": "main", "title": "Main", "parts": list(PNS)}]})
    report = operations.check_order(operations.normalize({"operations": items}), inventory)
    outcomes = {row["outcome"] for row in report.outcomes}
    return "fail" if "fail" in outcomes else "unknown" if "unknown" in outcomes else "pass"


def _pair(pair_id: str, dropped: str, defect: str) -> Pair:
    def run(mutant: bool) -> str:
        return _outcome(dropped if mutant else None)

    return Pair(pair_id, defect, run)


FAMILY = Family(
    id="install_missing",
    check="skills/cad-record/scripts/assembly_operations.py check_order",
    owner="pkgE",
    pairs=(
        _pair("bracket", "mount-bracket", "the bracket's installing join is missing and nothing else names it"),
        _pair("screw", "fasten-cover", "the cover screw's installing fasten is missing and nothing else names it"),
        _pair("cover", "fit-cover", "the cover's installing close is missing and the screw still fastens into it"),
        _pair("bearing", "seat-bearing", "the bearing's installing press fit is missing and the cover still closes over it"),
    ),
)
