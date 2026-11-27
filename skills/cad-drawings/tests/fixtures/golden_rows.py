# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Goldens for the frame-aligned drawings that must not change (design T8).

A golden is the part of a drawing result a reader of the sheet depends on: its status, the characteristic rows of
``coverage.json`` (id, requirement, value rounded to ``PLACES``, quantity, axis, view, status and printed
tolerance) and the finding codes. Faces, annotation ids and hashes are left out: they move with the kernel
build, not with the drawing. Write one with ``CAD_UPDATE_GOLDEN=1``; review the diff before it is committed.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).with_name("golden")
PLACES = 3   # a drawing prints lengths to 0.001 mm at most


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """The comparable part of one ``parts`` entry of the drawing CLI's output."""
    coverage = Path(result["output_dir"]) / "coverage.json"
    rows = json.loads(coverage.read_text(encoding="utf-8"))["rows"] if coverage.is_file() else []
    kept = [{"characteristic": row["characteristic"], "requirement": row["requirement"],
             "value_mm": round(row["value_mm"], PLACES) if isinstance(row.get("value_mm"), (int, float)) else None,
             "quantity": row.get("quantity"), "axis": row.get("axis"), "view": row.get("view"),
             "status": row["status"],
             "tolerance": {key: (row.get("tolerance") or {}).get(key)
                           for key in ("kind", "designation", "lower_mm", "upper_mm")}}
            for row in rows]
    return {"status": result["status"], "rows": sorted(kept, key=lambda row: row["characteristic"]),
            "findings": sorted([item["code"], item.get("feature") or ""] for item in result.get("findings", []))}


def check(case: unittest.TestCase, name: str, result: dict[str, Any]) -> None:
    """Compare the result's summary with golden ``name``; a missing golden fails the test."""
    actual = summary(result)
    path = GOLDEN / f"{name}.json"
    if os.environ.get("CAD_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not path.is_file():
        case.fail(f"no golden {path.name}; write it with CAD_UPDATE_GOLDEN=1 once the drawing is as intended")
    case.assertEqual(actual, json.loads(path.read_text(encoding="utf-8")))
