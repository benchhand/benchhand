# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-model's part store: the cad-record store (`cad-record/scripts/store.py`), keyed by `layout.input_key`.

A part build and a manufacturing make are cached computations. Their records' members are the output files
(`step`, `manufacture_step`, or a manufacturing folder's file names) and their facts are the runner's result; the
key is the sha256 of exactly the inputs each one names below, never a timing or a path of the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cadmodel.record import RECORD_SCRIPTS, _load, load_layout

STORE_SCRIPT = RECORD_SCRIPTS / "store.py"
PART_INPUTS = ("format", "kind", "kernel", "runtime", "request", "inputs")
MANUFACTURING_INPUTS = ("format", "kind", "entry", "state", "features", "process")


def load_store() -> Any:
    """cad-record `store.py` under the name `cad_record_store`."""
    return _load("cad_record_store", STORE_SCRIPT)


def key(material: Mapping[str, Any]) -> str:
    """The store key of a part build or a manufacturing make: `layout.input_key` of its allowlisted inputs."""
    allow = PART_INPUTS if material.get("kind") == "part" else MANUFACTURING_INPUTS
    return str(load_layout().input_key(material, allow=allow))


def file_sha(path: Path) -> str:
    return str(load_store().file_sha(path))


def Store(hw: Path) -> Any:  # noqa: N802 - the cad-record class, constructed under its own name
    return load_store().Store(hw)
