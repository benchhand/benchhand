#!/usr/bin/env python3
"""The static check's cad.readback bundle is matched by a recipe that covers the check's code and data (#128).

publish_readback reuses a sealed bundle whose recipe equals the new one. The recipe names the check
code (check.py and the modules it loads, manufacture.py among them) and VERDICT_DATA, so after an
edit to either a rerun republishes the packet instead of keeping the bundle the old code sealed. The
edit is simulated by changing the bytes code_shas reads for one file. Kernel-free; the publication
itself is covered in test_cad_model.ManufacturingInputCacheTests.
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
ISO286 = SKILLS / "cad-fits" / "data" / "iso286.json"
ISO286_IT6_EDIT = (b'"6": [6, ', b'"6": [7, ')  # one table value: IT6 up to 3 mm, 6 um -> 7 um


@contextlib.contextmanager
def edited(path: Path, change: tuple[bytes, bytes] | None = None) -> Iterator[None]:
    """code_shas reads `path` edited: one comment line appended, or the one `change` (old, new) applied."""
    target, read = path.resolve(), code_shas._read

    def read_edited(candidate: Path) -> bytes:
        data = read(candidate)
        if Path(candidate).resolve() != target:
            return data
        if change is None:
            return data + b"\n# edited\n"
        assert data.count(change[0]) == 1, f"{path} does not hold {change[0]!r} once"
        return data.replace(*change)

    code_shas._closure.cache_clear()   # code_shas reads each script once per process
    try:
        with mock.patch.object(code_shas, "_read", read_edited):
            yield
    finally:
        code_shas._closure.cache_clear()


def covered(path: Path, declared: tuple[Path, ...]) -> bool:
    path = path.resolve()
    return any(path == entry.resolve() or path.is_relative_to(entry.resolve()) for entry in declared)


class ReadbackRecipeTests(unittest.TestCase):
    def test_editing_the_check_code_or_its_data_changes_the_readback_recipe(self) -> None:
        layout = record.load_layout()
        before = layout.recipe_sha256(check.readback_recipe(layout, PACKET))
        self.assertEqual(layout.recipe_sha256(check.readback_recipe(layout, PACKET)), before)
        for path, change in ((SCRIPTS / "cadmodel" / "check.py", None), (SCRIPTS / "cadmodel" / "manufacture.py", None),
                             (SCRIPTS / "cadmodel" / "occ.py", None), (ISO286, ISO286_IT6_EDIT),
                             (SKILLS / "cad-model" / "assets" / "materials.yaml", None)):
            with self.subTest(edited=path.name), edited(path, change):
                self.assertNotEqual(layout.recipe_sha256(check.readback_recipe(layout, PACKET)), before)

    def test_the_declared_data_covers_every_data_file_the_check_code_names(self) -> None:
        check_py = SCRIPTS / "cadmodel" / "check.py"
        missing = sorted(str(path.relative_to(SKILLS)) for path in code_shas.referenced_data(check_py)
                         if not covered(path, check.VERDICT_DATA))
        self.assertEqual(missing, [], "add these to check.VERDICT_DATA")


if __name__ == "__main__":
    unittest.main()
