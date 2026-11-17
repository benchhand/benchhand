#!/usr/bin/env python3
"""The static check's cad.readback bundle is matched by a recipe that covers the check's code (#128).

publish_readback reuses a sealed bundle whose recipe equals the new one. The recipe names the check
code (check.py and the modules it loads, manufacture.py among them), so after an edit to that code a
rerun republishes the packet instead of keeping the bundle the old code sealed. The edit is simulated
by appending a comment to the bytes code_shas reads for one file. Kernel-free.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))
from cadmodel import check, record  # noqa: E402 - imports without the kernel

_spec = importlib.util.spec_from_file_location("cad_motion_code_shas", SKILLS / "cad-motion" / "scripts" / "code_shas.py")
assert _spec is not None and _spec.loader is not None
code_shas = sys.modules.get(_spec.name)
if code_shas is None:
    code_shas = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = code_shas
    _spec.loader.exec_module(code_shas)

PACKET = {"producer": check.READBACK_PRODUCER, "complete": True, "outcomes": []}


@contextlib.contextmanager
def edited(path: Path) -> Iterator[None]:
    """code_shas reads `path` with one comment line appended, as after an edit that changes no behavior."""
    target, read = path.resolve(), code_shas._read

    def read_edited(candidate: Path) -> bytes:
        data = read(candidate)
        return data + b"\n# edited\n" if Path(candidate).resolve() == target else data

    code_shas._closure.cache_clear()   # code_shas reads each script once per process
    try:
        with mock.patch.object(code_shas, "_read", read_edited):
            yield
    finally:
        code_shas._closure.cache_clear()


class ReadbackRecipeTests(unittest.TestCase):
    def test_editing_the_check_code_changes_the_readback_recipe(self) -> None:
        layout = record.load_layout()
        before = layout.recipe_sha256(check.readback_recipe(layout, PACKET))
        self.assertEqual(layout.recipe_sha256(check.readback_recipe(layout, PACKET)), before)
        for path in (SCRIPTS / "cadmodel" / "check.py", SCRIPTS / "cadmodel" / "manufacture.py",
                     SCRIPTS / "cadmodel" / "occ.py"):
            with self.subTest(edited=path.name), edited(path):
                self.assertNotEqual(layout.recipe_sha256(check.readback_recipe(layout, PACKET)), before)


if __name__ == "__main__":
    unittest.main()
