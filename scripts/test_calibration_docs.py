# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no agent-facing text gives an unconditional calibration instruction.

Calibration is off for release (CAD_CALIBRATION). Generic guideline values govern, and a tighter limit comes from
a test coupon. The agent profiles (sources and generated copies), the SKILL.md files of cad-design and cad-record and the
reference pages the agent reads may mention calibration only in a sentence that carries the "when `CAD_CALIBRATION=1`"
note. A backtick span (a finding code such as `process.fdm.wall_uncalibrated`) is not prose and is skipped.

Run: python3 -m unittest discover -s scripts -p test_calibration_docs.py
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTE = "`CAD_CALIBRATION=1`"
PAGES = (
    "skills/cad-design/SKILL.md",
    "skills/cad-design/references/process-checks.md",
    "skills/cad-design/references/fidelity.md",
    "skills/cad-record/SKILL.md",
    "skills/cad-record/references/manufacturing-intent.md",
)
PROFILE_DIRS = ("agents", "harness/claude", "harness/codex", "harness/opencode", "harness/kiro")
CODE_SPAN = re.compile(r"`[^`\n]*`")
MENTION = re.compile(r"calibrat", re.IGNORECASE)


def _shown(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name


SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`|*(\[])")


def unconditional(path: Path) -> list[str]:
    """The sentences of `path` that mention calibration in prose without the flag note in the same sentence."""
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for sentence in SENTENCE_END.split(line):
            if NOTE not in sentence and MENTION.search(CODE_SPAN.sub("", sentence)):
                found.append(f"{_shown(path)}:{number}: {sentence.strip()[:100]}")
    return found


def profile_files() -> list[Path]:
    return sorted(p for d in PROFILE_DIRS for p in (ROOT / d).rglob("*") if p.is_file() and p.suffix in (".md", ".toml", ".json"))


class CalibrationDocsTests(unittest.TestCase):
    def test_the_pages_and_profiles_to_scan_exist(self) -> None:
        for page in PAGES:
            self.assertTrue((ROOT / page).is_file(), page)
        self.assertGreater(len(profile_files()), 7)

    def test_a_line_that_mixes_the_note_with_an_unconditional_instruction_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.md"
            page.write_text(
                "When `CAD_CALIBRATION=1`, use the recorded calibration. Calibrate the printer before you print.\n"
                "When `CAD_CALIBRATION=1`, use the recorded calibration. Otherwise guideline values govern.\n",
                encoding="utf-8")
            found = unconditional(page)
        self.assertEqual(len(found), 1, found)
        self.assertIn("page.md:1:", found[0])
        self.assertIn("Calibrate the printer", found[0])

    def test_no_page_or_agent_profile_gives_an_unconditional_calibration_instruction(self) -> None:
        found = [line for path in (*(ROOT / p for p in PAGES), *profile_files()) for line in unconditional(path)]
        self.assertEqual(found, [], "mention calibration only under the `CAD_CALIBRATION=1` note")


if __name__ == "__main__":
    unittest.main()
