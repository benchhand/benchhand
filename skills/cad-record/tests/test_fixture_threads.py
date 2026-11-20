#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The shared fixture loader hands every thread a fully executed module, never a half-loaded one."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "manufacturing_intent.py"

# A module body that stalls after it starts until the second thread is about to load it.
SLOW_BODY = """
import sys, time
sys._fixture_probe["started"].set()
sys._fixture_probe["second_ready"].wait(10)
time.sleep(0.2)
use_case_questions = "ready"
"""


class FixtureLoaderThreadTests(unittest.TestCase):
    def test_second_thread_waits_for_a_fully_executed_module(self) -> None:
        spec = importlib.util.spec_from_file_location("cad_record_thread_fixture_loader", FIXTURE)
        assert spec and spec.loader
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        name = "cad_record_thread_probe_module"
        probe = {"started": threading.Event(), "second_ready": threading.Event()}
        sys._fixture_probe = probe  # type: ignore[attr-defined]
        self.addCleanup(lambda: delattr(sys, "_fixture_probe"))
        self.addCleanup(sys.modules.pop, name, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slow.py"
            path.write_text(SLOW_BODY, encoding="utf-8")

            def second() -> bool:
                probe["started"].wait(10)
                probe["second_ready"].set()
                return hasattr(loader._module(name, path), "use_case_questions")

            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(lambda: hasattr(loader._module(name, path), "use_case_questions"))
                later = pool.submit(second)
                self.assertTrue(first.result(30))
                self.assertTrue(later.result(30))

    def test_failed_load_leaves_no_partial_module(self) -> None:
        spec = importlib.util.spec_from_file_location("cad_record_thread_fixture_loader2", FIXTURE)
        assert spec and spec.loader
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        name = "cad_record_failing_probe_module"
        self.addCleanup(sys.modules.pop, name, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.py"
            path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                loader._module(name, path)
        self.assertNotIn(name, sys.modules)


if __name__ == "__main__":
    unittest.main()
