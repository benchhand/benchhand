#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-record's shared module loader: a failed load leaves nothing behind, so a retry runs the module again.

The worker-thread case is in skills/cad-drawings/tests/test_shared_module.py, which loads cad-fits through it.
Run: uv run --no-project --with-requirements harness/test-requirements.txt python -B -m unittest discover -s skills/cad-record/tests -p test_shared_module.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import shared_module  # noqa: E402  the scripts folder goes on sys.path first, as the skills that use it do


FAILS_ONCE = """
import sys
probe = sys._fits_probe
probe["runs"].append(1)
if len(probe["runs"]) == 1:
    PARTIAL = True
    raise RuntimeError("first load fails")
READY = True
"""


class FailedLoadTests(unittest.TestCase):
    def test_retry_after_a_failed_load_runs_the_body_again(self) -> None:
        name = "cad_shared_module_fails_once"
        probe: dict[str, list[int]] = {"runs": []}
        sys._fits_probe = probe  # type: ignore[attr-defined]
        self.addCleanup(lambda: delattr(sys, "_fits_probe"))
        self.addCleanup(sys.modules.pop, name, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fails_once.py"
            path.write_text(FAILS_ONCE, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                shared_module.load_shared(name, path)
            self.assertFalse(name in sys.modules, "a failed load left the half-built module behind")
            module = shared_module.load_shared(name, path)
        self.assertEqual(len(probe["runs"]), 2, "the second load ran the body again")
        self.assertTrue(getattr(module, "READY", False))
        self.assertFalse(hasattr(module, "PARTIAL"), "the retry got a fresh module, not the half-built one")


if __name__ == "__main__":
    unittest.main()
