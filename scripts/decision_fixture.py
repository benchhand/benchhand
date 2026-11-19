# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Test helper: write the MADR 4.0.0 decision records that hw/decisions/ holds in a test project.

Tests put this directory on sys.path (see modload.py). A call replaces the record with the same DR id."""

from __future__ import annotations

import datetime
from pathlib import Path

from modload import load_module

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "cad-record" / "scripts"
records = load_module("cad_record_decision_records", SCRIPTS / "decision_records.py")


def write_test_decision(hw: Path, ident: str, *, status: str = "accepted", title: str | None = None,
                        context: str = "A test decision.") -> Path:
    """`hw`/decisions/`ident`-slug.md with the given MADR status, replacing an earlier record of that id."""
    folder = Path(hw) / "decisions"
    for old in folder.glob(f"{ident}-*.md"):
        old.unlink()
    return records.write_decision(
        folder, number=int(ident.removeprefix("DR-")), title=title or f"Test decision {ident}", status=status,
        date=datetime.date(2026, 1, 1), context=context, options=("The test option",), chosen="The test option",
        justification="the test needs a record")


def decision_path(hw: Path, ident: str) -> Path:
    """The file of record `ident` under `hw`/decisions/."""
    (found,) = (Path(hw) / "decisions").glob(f"{ident}-*.md")
    return found
