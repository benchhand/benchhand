# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""A seam for test_cad_model's KeptFileRecoveryTests, active only while this folder is on PYTHONPATH and
CAD_TEST_STORE_SEAM names folders (a JSON list such as ["parts", "manufacturing"]).

Once `cad_model.py run` loads cad-record `store.py`, the first `place_object` into each named folder of a staged
version fails its hash. During its undo, a writer that takes no lock renames FOREIGN1 onto the target just
before the undo moves it aside, and FOREIGN2 just before the undo puts FOREIGN1 back. The undo then cannot put
FOREIGN1 back, so the store keeps it. Every other call runs unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
from typing import Any

_FOLDERS = os.environ.get("CAD_TEST_STORE_SEAM")


class _Mismatch:
    """A digest that matches no object."""

    @staticmethod
    def hexdigest() -> str:
        return "0" * 64


def _arm(store: Any, folders: list[str]) -> None:
    pending, active = list(folders), {}
    place_object, rename2, file_digest = store.Store.place_object, store._rename2, hashlib.file_digest

    def foreign(number: int) -> None:
        target, folder = active["target"], active["folder"]
        made = target.with_name(f"foreign{number}.tmp")
        made.write_bytes(f"FOREIGN{number} {folder}".encode())
        os.rename(made, target)

    def placing(self: Any, digest: str, target: Path) -> bool:
        target = Path(target)
        staged = any(".partial-" in part for part in target.parts)
        folder = next((f for f in pending if staged and f in target.parts), None)
        if folder is None:
            return bool(place_object(self, digest, target))
        pending.remove(folder)
        active.update(target=target, folder=folder)
        try:
            return bool(place_object(self, digest, target))
        finally:
            active.clear()

    def hashing(stream: Any, *args: Any, **kwargs: Any) -> Any:
        found = file_digest(stream, *args, **kwargs)
        return _Mismatch() if active and str(getattr(stream, "name", "")).endswith(".link") else found

    def renaming(source: Path, target: Path, **kwargs: Any) -> None:
        if active and Path(source) == active["target"]:
            foreign(1)  # just before the undo moves the target aside
        elif active and Path(target) == active["target"] and Path(source).name.endswith(".aside"):
            foreign(2)  # just before the undo puts the moved-aside file back
        rename2(source, target, **kwargs)

    store.Store.place_object, store._rename2, hashlib.file_digest = placing, renaming, hashing


if _FOLDERS:
    _exec_module = importlib.machinery.SourceFileLoader.exec_module

    def _exec_then_arm(self: Any, module: Any) -> None:
        _exec_module(self, module)
        if module.__name__ == "cad_record_store":
            _arm(module, json.loads(_FOLDERS))

    importlib.machinery.SourceFileLoader.exec_module = _exec_then_arm  # type: ignore[method-assign]
