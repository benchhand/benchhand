# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no agent-facing text gives an unconditional calibration instruction.

Calibration is off for release (CAD_CALIBRATION). Generic guideline values govern, and a tighter limit comes from
a test coupon. The agent profiles (sources and generated copies), the SKILL.md files of cad-design and cad-record and the
reference pages the agent reads may mention calibration only in a sentence that carries the "when `CAD_CALIBRATION=1`"
note. Backticked text counts: a finding code is scanned like prose. A clause ends at `;` or at a sentence end before a capital, quote, digit or backtick.

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
MENTION = re.compile(r"calibrat", re.IGNORECASE)


def _shown(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name


CLAUSE_END = re.compile(r";\s*|(?<=[.!?])\s+(?=[A-Z0-9\"'`|*(\[])")


def scanned_files() -> list[Path]:
    skills = [*ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("skills/*/references/*.md")]
    yaml = (".yaml", ".yml")
    examples = [p for p in (ROOT / "examples").rglob("*") if p.is_file() and p.suffix in yaml]
    templates = [p for p in ROOT.glob("skills/*/assets/**/*") if p.is_file() and p.suffix in yaml]
    return sorted({*(ROOT / p for p in PAGES), *skills, *profile_files(), *examples, *templates})


# Mentions that are not a printer-calibration instruction: finding codes, a calibrated measuring instrument or
# capability, and descriptions of what a record holds.
NOT_AN_INSTRUCTION = re.compile(
    r"\w*uncalibrated\w*|calibrated (?:instrument|capability)|calibrated/demonstrated|calibration records"
    r"|their calibrations|never calibrations|calibration and test bytes|A measured calibration",
    re.IGNORECASE,
)


def logical_lines(path: Path) -> list[tuple[int, str]]:
    """(line number, text) pairs. A YAML file contributes only its comments, each run of them joined as one paragraph."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.suffix not in (".yaml", ".yml"):
        return list(enumerate(lines, 1))
    paragraphs: list[tuple[int, str]] = []
    run = False
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text.startswith("#"):
            run = False
            continue
        text = text.lstrip("# ").rstrip()
        if run:
            paragraphs[-1] = (paragraphs[-1][0], f"{paragraphs[-1][1]} {text}")
        else:
            paragraphs.append((number, text))
        run = True
    return paragraphs


def unconditional(path: Path) -> list[str]:
    """The clauses of `path` that mention calibration, backticked text included, without the flag note in the clause."""
    found = []
    for number, line in logical_lines(path):
        for sentence in CLAUSE_END.split(line):
            if NOTE not in sentence and MENTION.search(NOT_AN_INSTRUCTION.sub("", sentence)):
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

    def test_clauses_split_on_semicolons_quotes_and_digits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.md"
            page.write_text(
                "When `CAD_CALIBRATION=1`, read it; calibrate the printer.\n"
                'When `CAD_CALIBRATION=1`, read it. "Calibrate" the printer.\n'
                "When `CAD_CALIBRATION=1`, read it. 3 calibration prints first.\n"
                "When `CAD_CALIBRATION=1`, read it. See `calibrate the printer`.\n",
                encoding="utf-8")
            found = unconditional(page)
        self.assertEqual([f.split(":")[1] for f in found], ["1", "2", "3", "4"], found)

    def test_every_skill_page_and_example_comment_is_scanned(self) -> None:
        shown = {_shown(p) for p in scanned_files()}
        for expected in ("skills/cad-fits/SKILL.md", "skills/cad-design/references/process-checks.md"):
            self.assertIn(expected, shown)
        self.assertTrue(any(s.startswith("examples/") for s in shown), "no examples file scanned")

    def test_no_page_or_agent_profile_gives_an_unconditional_calibration_instruction(self) -> None:
        found = [line for path in scanned_files() for line in unconditional(path)]
        self.assertEqual(found, [], "mention calibration only under the `CAD_CALIBRATION=1` note")


if __name__ == "__main__":
    unittest.main()
