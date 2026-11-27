# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The `record` commands of docs/troubleshooting.md settle the project each is written for.

The guide gives one `record` call for a project that the record rules out of FDM and one for an FDM project. This
test runs each block exactly as written (through `sh`, with CAD_SKILLS set) in a scratch `hw/`, then runs `check`.
It lives here, not under skills/*/tests: a skill test may not read docs (test_docs_guard.py), because a docs-only
change skips the skill shards.

Run: python3 -m unittest discover -s scripts -p test_troubleshooting_docs.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "troubleshooting.md"
SCRIPT = 'flow_profile.py" record'
PRODUCT = "record_version: 2\nid: tst-product\nflow:\n  stage: intent\n  recheck_from: intent\nunits: mm\n"


def record_blocks() -> list[str]:
    """The fenced `sh` blocks of the guide that run `flow_profile.py record`, dedented."""
    blocks = re.findall(r"^( *)```sh\n(.*?)^\1```", GUIDE.read_text(encoding="utf-8"), re.S | re.M)
    return [re.sub(r"^ {%d}" % len(indent), "", body, flags=re.M) for indent, body in blocks if SCRIPT in body]


@unittest.skipUnless(shutil.which("uv"), "uv runs the guide's commands")
class GuideRecordCommands(unittest.TestCase):
    def run_in(self, hw_parent: Path, command: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "CAD_SKILLS": str(ROOT / "skills")}
        return subprocess.run(["sh", "-c", command], cwd=hw_parent, env=env, capture_output=True, text=True, timeout=300)

    def test_each_record_block_settles_the_project_it_is_written_for(self) -> None:
        blocks = record_blocks()
        self.assertEqual(len(blocks), 2, blocks)
        for plan, block in (("cnc", blocks[0]), ("fdm", blocks[1])):
            with self.subTest(plan), tempfile.TemporaryDirectory(prefix="guide-record-") as temporary:
                hw = Path(temporary) / "hw"
                hw.mkdir()
                (hw / "product.yaml").write_text(PRODUCT + f"manufacturing:\n  processes: [{plan}]\n", encoding="utf-8")
                recorded = self.run_in(Path(temporary), block)
                self.assertEqual(recorded.returncode, 0, recorded.stderr)
                checked = self.run_in(Path(temporary), 'uv run --script "$CAD_SKILLS/cad-flow/scripts/flow_profile.py" '
                                                       "check --hw hw --stage specify")
                self.assertEqual((checked.returncode, json.loads(checked.stdout)["problems"]), (0, []), checked.stdout)


if __name__ == "__main__":
    unittest.main()
