"""Every host script's PEP 723 requires-python floor is 3.12 or later, unless FreeCAD runs it on 3.11."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOB_LIST = ROOT / "scripts" / "freecad_job_modules.txt"
BLOCK = re.compile(r"^# /// script\n((?:#.*\n)+?)# ///$", re.MULTILINE)
FLOOR = re.compile(r"""^#\s*requires-python\s*=\s*["']\s*(?:>=|==)\s*(\d+)\.(\d+)""", re.MULTILINE)
MINIMUM = (3, 12)


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


def below_minimum(sources: dict[str, str], exempt: frozenset[str] | set[str]) -> list[str]:
    low = []
    for rel, source in sources.items():
        floor = floor_of(source)
        if rel not in exempt and floor is not None and floor < MINIMUM:
            low.append(f"{rel}: requires-python floor is {floor[0]}.{floor[1]}")
    return low


def header(spec: str) -> str:
    return f'# /// script\n# requires-python = "{spec}"\n# ///\n'


class FloorParsing(unittest.TestCase):
    def test_reads_floor_from_each_specifier_form(self) -> None:
        cases = {
            ">=3.12,<3.15": (3, 12),
            ">=3.11": (3, 11),
            "==3.12.*": (3, 12),
            "==3.11.*": (3, 11),
            "== 3.12.*": (3, 12),
        }
        for spec, expected in cases.items():
            with self.subTest(spec=spec):
                self.assertEqual(floor_of(header(spec)), expected)

    def test_header_without_requires_python_has_no_floor(self) -> None:
        self.assertIsNone(floor_of("# /// script\n# dependencies = []\n# ///\n"))

    def test_file_without_script_block_has_no_floor(self) -> None:
        self.assertIsNone(floor_of('requires_python = ">=3.11"\n'))

    def test_exact_pin_below_minimum_is_rejected(self) -> None:
        sources = {"scripts/new_tool.py": header("==3.11.*")}
        self.assertEqual(
            below_minimum(sources, frozenset()), ["scripts/new_tool.py: requires-python floor is 3.11"]
        )


class HostScriptFloors(unittest.TestCase):
    def test_host_scripts_require_python_3_12_or_later(self) -> None:
        sources = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in tracked_python()}
        low = below_minimum(sources, job_modules())
        self.assertEqual(
            low,
            [],
            "Raise the floor to >=3.12 or, if FreeCAD runs the file on its Python 3.11, "
            "add it to scripts/freecad_job_modules.txt:\n" + "\n".join(low),
        )


if __name__ == "__main__":
    unittest.main()
