"""Every host script's PEP 723 requires-python floor is 3.12 or later, unless FreeCAD runs it on 3.11."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOB_LIST = ROOT / "scripts" / "freecad_job_modules.txt"
BLOCK = re.compile(r"^# /// script\n((?:#.*\n)+?)# ///$", re.MULTILINE)
FLOOR = re.compile(r"""^#\s*requires-python\s*=\s*["']\s*>=\s*(\d+)\.(\d+)""", re.MULTILINE)
MINIMUM = (3, 12)
# Host files PR #62 left on their old floor because the python-pin branch (D6) owns them. Remove an
# entry when its floor is raised; the last test fails on an entry that is no longer below the minimum.
PENDING_D6 = frozenset(
    {
        "skills/cad-model/scripts/cad_model.py",
        "skills/cad-model/tests/fixtures/components_probe.py",
        "skills/cad-model/tests/fixtures/containment_probe.py",
        "skills/cad-model/tests/fixtures/holes_probe.py",
        "skills/cad-model/tests/fixtures/mesh_probe.py",
        "skills/cad-model/tests/fixtures/share_probe.py",
        "skills/cad-model/tests/fixtures/suspect_probe.py",
        "skills/cad-motion/scripts/access.py",
        "skills/cad-motion/scripts/freecad_assembly.py",
        "skills/cad-motion/scripts/merge_sweeps.py",
        "skills/cad-motion/scripts/motion.py",
        "skills/cad-motion/scripts/play.py",
        "skills/cad-motion/scripts/pose.py",
        "skills/cad-motion/scripts/spot_check.py",
        "skills/cad-motion/scripts/sweep.py",
        "skills/cad-motion/tests/fixtures/access_fixture.py",
        "skills/cad-motion/tests/fixtures/build_fixtures.py",
        "skills/cad-motion/tests/fixtures/continuous_collision.py",
        "skills/cad-motion/tests/fixtures/measure_probe.py",
        "skills/cad-motion/tests/fixtures/mesh_probe.py",
        "skills/cad-motion/tests/fixtures/play_fixture.py",
        "skills/cad-motion/tests/fixtures/probe_step.py",
        "skills/cad-motion/tests/fixtures/step_header_probe.py",
    }
)


def job_modules() -> set[str]:
    lines = (line.strip() for line in JOB_LIST.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


def floor_of(source: str) -> tuple[int, int] | None:
    block = BLOCK.search(source)
    match = FLOOR.search(block.group(1)) if block else None
    return (int(match.group(1)), int(match.group(2))) if match else None


class FloorParsing(unittest.TestCase):
    def test_reads_lower_bound_of_script_block(self) -> None:
        source = '#!/usr/bin/env -S uv run --script\n# /// script\n# requires-python = ">=3.11,<3.15"\n# ///\n'
        self.assertEqual(floor_of(source), (3, 11))

    def test_file_without_script_block_has_no_floor(self) -> None:
        self.assertIsNone(floor_of('requires_python = ">=3.11"\n'))


class HostScriptFloors(unittest.TestCase):
    def test_host_scripts_require_python_3_12_or_later(self) -> None:
        exempt = job_modules() | PENDING_D6
        low = []
        for rel in tracked_python():
            if rel in exempt:
                continue
            floor = floor_of((ROOT / rel).read_text(encoding="utf-8"))
            if floor is not None and floor < MINIMUM:
                low.append(f"{rel}: requires-python floor is {floor[0]}.{floor[1]}")
        self.assertEqual(
            low,
            [],
            "Raise the floor to >=3.12 or, if FreeCAD runs the file on its Python 3.11, "
            "add it to scripts/freecad_job_modules.txt:\n" + "\n".join(low),
        )

    def test_pending_entries_are_still_below_the_minimum(self) -> None:
        stale = [
            rel
            for rel in sorted(PENDING_D6)
            if (floor := floor_of((ROOT / rel).read_text(encoding="utf-8"))) is None or floor >= MINIMUM
        ]
        self.assertEqual(stale, [], "Remove these from PENDING_D6; their floor is already 3.12 or later.")


if __name__ == "__main__":
    unittest.main()
