# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Modelled dimensions of common commodity hardware, from ../data/commodity.json.

Usage: uv run --script commodity.py DESIGNATION

DESIGNATION is a standard or family and a thread size, such as "ISO 4762 M4",
"ISO 4032 M5", "ISO 8734 6" or "heat_set_insert M3"; a trailing length ("ISO 4762 M4x20")
is accepted and echoed back. Standards: ISO 4762, ISO 7380-1, ISO 4032, ISO 7089, ISO 8734, DIN 653 (M3 to M8).
Sizes: M2 to M8 (dowel diameters 2 to 8). Provenance in ../data/README.md.

stdout: JSON with the item, its named dimensions in mm and, for a threaded size, the pitch,
ISO 273 medium clearance hole and tap drill. stderr: diagnostics.
Exit 0 = found, 2 = unknown designation or size.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data" / "commodity.json"
DESIGNATION = re.compile(r"^(?P<name>.+?)\s+(?P<size>M?\d+(?:\.\d+)?)(?:\s*[xX×]\s*(?P<length>\d+(?:\.\d+)?))?$")


class CommodityError(ValueError):
    """The designation or size is outside the table."""


@cache
def table() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return data


def lookup(designation: str) -> dict[str, Any]:
    """Modelled dimensions of `designation`; raises CommodityError when the table does not hold it."""
    match = DESIGNATION.match(designation.strip())
    if match is None:
        raise CommodityError(f"cannot read {designation!r}: expected '<standard or family> <size>[x<length>]'")
    name, size = match["name"], match["size"]
    entries = {**table()["standards"], **table()["families"]}
    entry = entries.get(name)
    if entry is None:
        raise CommodityError(f"{name!r} is not in the table; known: {', '.join(sorted(entries))}")
    values = entry["size"].get(size)
    if values is None:
        raise CommodityError(f"{name} has no size {size!r}; known: {', '.join(entry['size'])}")
    result: dict[str, Any] = {"designation": name, "size": size, "item": entry["item"], "units": "mm",
                              "dimensions": dict(zip((f.strip() for f in entry["fields"].split(",")), values, strict=True))}
    if match["length"]:
        result["length"] = float(match["length"])
    if size in table()["thread"]["size"]:
        result["thread"] = table()["thread"]["size"][size]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="modelled dimensions of commodity hardware")
    parser.add_argument("designation", help='such as "ISO 4762 M4" or "heat_set_insert M3"')
    args = parser.parse_args(argv)
    try:
        result = lookup(args.designation)
    except CommodityError as error:
        print(f"commodity: {error}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
