# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no tracked test or script may bypass git hooks with --no-verify or a short -n commit flag.

A test repo that must not run host hooks sets ``core.hooksPath`` to an empty directory instead.
Scanned: tracked files under ``scripts/``, ``.github/``, ``harness/`` and any ``tests/`` directory or
``test_*`` file, except this guard. Markdown is not scanned, so docs may quote the flag to explain the rule.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve().relative_to(ROOT).as_posix()
SCANNED = re.compile(r"^(scripts/|\.github/|harness/)|(^|/)tests/|(^|/)test_[^/]*$")
LONG_FLAG = re.compile(r"--no-verify")
# a short option cluster holding n, on a line that commits: -n, -qn, -nm, "-qn"
SHORT_FLAG = re.compile(r"""(?<![\w-])-[a-zA-Z]*n[a-zA-Z]*(?![\w-])""")


def offences(path: str, text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if LONG_FLAG.search(line) or ("commit" in line and SHORT_FLAG.search(line)):
            found.append(f"{path}:{number}: {line.strip()}")
    return found


class NoVerifyGuardTests(unittest.TestCase):
    def test_no_tracked_test_or_script_bypasses_hooks(self) -> None:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split("\0")
        found: list[str] = []
        for path in filter(None, listing):
            if path == THIS or path.endswith(".md") or not SCANNED.search(path):
                continue
            file = ROOT / path
            if file.is_file():
                found += offences(path, file.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(found, [], "use core.hooksPath on an empty dir, not a bypass flag")

    def test_detector_flags_each_form(self) -> None:
        for line in ('git("commit", "-qm", "x", "--no-verify")', 'git commit -n -m x', 'git("commit", "-qn", "-m", "x")', "git push --no-verify"):
            self.assertTrue(offences("f", line), line)
        for line in ('git("commit", "-qm", "x")', "git commit --amend -m x", 'git("commit", "-m", "run-name")'):
            self.assertFalse(offences("f", line), line)


if __name__ == "__main__":
    unittest.main()
