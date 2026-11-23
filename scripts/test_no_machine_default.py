# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: with no machine profile the flow uses the shipped guideline values and asks no blocking printer question.

The GROUND step of cad-flow, the use-case reference and the project-start question about the printer may offer to
record the user's machine once, as an option. They never make a machine profile a precondition. Process and material
questions stay, and so does "do not assume an available printer" for process selection.

Run: python3 -m unittest discover -s scripts -p test_no_machine_default.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / "skills" / "cad-flow"
SKILL = (FLOW / "SKILL.md").read_text(encoding="utf-8")
SELECTION = (FLOW / "references" / "manufacturing-selection.md").read_text(encoding="utf-8")


def ground_line() -> str:
    return next(line for line in SKILL.splitlines() if line.lstrip().startswith("At GROUND, ask"))


def printer_question() -> str:
    out = subprocess.run([sys.executable, str(FLOW / "scripts" / "flow_profile.py"), "questions"],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    return next(line for line in out.splitlines() if line.startswith("4. "))


class NoMachineDefault(unittest.TestCase):
    def test_ground_step_uses_guideline_values_when_no_machine_is_recorded(self) -> None:
        line = ground_line()
        self.assertRegex(line, r"(?i)no machine profile")
        self.assertRegex(line, r"(?i)shipped guideline values govern")
        self.assertIn("`CAD_CALIBRATION=1`", line)

    def test_ground_step_offers_the_machine_once_and_never_blocks_on_it(self) -> None:
        line = ground_line()
        self.assertRegex(line, r"(?i)offer once")
        self.assertRegex(line, r"(?i)never blocks")

    def test_ground_step_keeps_process_selection_rules(self) -> None:
        line = ground_line()
        self.assertIn("focused use-case questions", line)
        self.assertRegex(line, r"(?i)do not assume an available printer, material or process")

    def test_use_case_reference_does_not_make_equipment_a_precondition(self) -> None:
        self.assertRegex(SELECTION, r"(?i)no machine profile")
        self.assertRegex(SELECTION, r"(?i)shipped guideline values")
        self.assertRegex(SELECTION, r"(?i)never blocks")

    def test_printer_question_is_an_optional_offer_that_waits_on_nothing(self) -> None:
        question = printer_question()
        self.assertRegex(question, r"(?i)optional")
        self.assertRegex(question, r"(?i)guideline values")
        self.assertNotRegex(question, r"^4\. Which printer")


if __name__ == "__main__":
    unittest.main()
