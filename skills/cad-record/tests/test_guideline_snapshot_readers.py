#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The snapshot readers of the motion and analysis producers read a version's record/ in snapshot mode (benchhand-lch,
round 4). Each reader's `assess` is called with a spy in place of `load_record`, so the test sees exactly how the reader
asks for the record."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_module as load  # noqa: E402

mi = load("cad_record_manufacturing_intent", SKILLS / "cad-record" / "scripts" / "manufacturing_intent.py")


class Stop(Exception):
    """Raised by the spy once the reader has asked for the record."""


def modules() -> dict[str, Any]:
    found: dict[str, Any] = {}
    sys.path.insert(0, str(SKILLS / "cad-analysis" / "scripts"))
    for name, path in (("play", SKILLS / "cad-motion" / "scripts" / "play.py"),
                       ("access", SKILLS / "cad-motion" / "scripts" / "access.py"),
                       ("stop_reaction", SKILLS / "cad-analysis" / "scripts" / "stop_reaction.py")):
        try:
            found[name] = load(f"cad_lch_snapshot_{name}", path)
        except ImportError:
            continue
    return found


class SnapshotMode(unittest.TestCase):
    def asked(self, module: Any, version: str | None) -> dict[str, Any]:
        """The keyword arguments `module.assess` passes to load_record for a run on `version`."""
        seen: dict[str, Any] = {}

        def spy(hw: Path, **kwargs: Any) -> Any:
            seen.update(kwargs)
            raise Stop

        with tempfile.TemporaryDirectory() as directory:
            record_hw = Path(directory)
            (record_hw / "assembly.yaml").write_text("operations: []\n", encoding="utf-8")
            args = argparse.Namespace(version=version, hw=record_hw, version_dir=record_hw, record_hw=record_hw)
            with mock.patch.object(mi, "load_record", spy), self.assertRaises(Stop):
                module.assess(args, version, record_hw)
        return seen

    def test_each_producer_reads_a_version_in_snapshot_mode_and_the_live_record_without_it(self) -> None:
        readers = modules()
        if not readers:
            self.skipTest("the producers cannot be imported here")
        for name, module in readers.items():
            with self.subTest(reader=name, version="v1"):
                self.assertTrue(self.asked(module, "v1").get("snapshot"), f"{name} reads a version's record/ without snapshot mode")
            with self.subTest(reader=name, version=None):
                self.assertFalse(self.asked(module, None).get("snapshot", False))


if __name__ == "__main__":
    unittest.main()
