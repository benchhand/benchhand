# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""import_check reports unavailable packages as skipped and fails on every other import error."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_check  # noqa: E402


class ImportOne(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        original = import_check.ROOT
        import_check.ROOT = self.root
        self.addCleanup(setattr, import_check, "ROOT", original)

    def module(self, source: str) -> Path:
        path = self.root / "skills" / "demo" / "scripts" / "mod_under_test.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_clean_import_has_no_skip_reason(self) -> None:
        self.assertIsNone(import_check.import_one(self.module("VALUE = 1\n")))

    def test_freecad_import_is_skipped_with_reason(self) -> None:
        reason = import_check.import_one(self.module("import FreeCAD\n"))
        self.assertEqual(reason, "FreeCAD: FreeCAD runtime only")

    def test_other_missing_package_fails(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_check.import_one(self.module("import not_a_real_package_xyz\n"))

    def test_unhashable_dataclass_default_fails(self) -> None:
        source = (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass A:\n    x: list = []\n"
        )
        with self.assertRaises(ValueError):
            import_check.import_one(self.module(source))


if __name__ == "__main__":
    unittest.main()
