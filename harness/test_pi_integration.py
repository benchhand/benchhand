#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Run the installed pi CAD agents in a real pi with pi-subagents, driven by a scripted model.

pi and pi-subagents come pinned from harness/pi/integration/package-lock.json
(`npm ci --prefix harness/pi/integration`). Without them these tests skip, and CAD_REQUIRE_PI=1
(set in CI) turns the skip into a failure. The model is pi-ai's faux provider
(`scripted-model.js`), so no model service or key is needed. A fake `uv` on PATH records each
call instead of running a skill script.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CAD = Path(__file__).resolve().parents[1]
INTEGRATION = CAD / "harness/pi/integration"
PI = INTEGRATION / "node_modules/.bin/pi"
SUBAGENTS = INTEGRATION / "node_modules/pi-subagents/index.js"
WEB_ACCESS = INTEGRATION / "node_modules/pi-web-access"
REQUIRED = os.environ.get("CAD_REQUIRE_PI") == "1"
AVAILABLE = PI.is_file() and SUBAGENTS.is_file() and shutil.which("node") is not None
BOM = "cad-bom/scripts/bom.py"
FAKE_UV = """#!/bin/sh
printf '%s\\n' "$*" >> {log}
pwd >> {log}
exit 0
"""


def setUpModule() -> None:
    if REQUIRED and not AVAILABLE:
        raise AssertionError(f"CAD_REQUIRE_PI=1 but pi is not installed: run `npm ci --prefix {INTEGRATION.relative_to(CAD)}`")


@unittest.skipUnless(AVAILABLE or REQUIRED, "pi is not installed; run `npm ci --prefix harness/pi/integration`")
class PiSubagentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.tmp = self.root / "tmp"
        self.bin = self.root / "bin"
        for folder in (self.home, self.project / "hw", self.project / ".cad-work/u1", self.tmp, self.bin):
            folder.mkdir(parents=True)
        self.uv_log = self.root / "uv.log"
        uv = self.bin / "uv"
        uv.write_text(FAKE_UV.format(log=json.dumps(str(self.uv_log))), encoding="utf-8")
        uv.chmod(0o755)
        self.log = self.root / "model.jsonl"
        self.parent_mark = self.root / "parent-ran"
        self.child_mark = self.root / "child-ran"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self, *scope: str) -> Path:
        done = subprocess.run([sys.executable, "-B", str(CAD / "harness/install.py"), "--harness", "pi", *scope],
                              capture_output=True, text=True, env={**os.environ, "HOME": str(self.home)}, cwd=self.project, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return self.home / ".pi/agent" if "--user" in scope else self.project / ".pi"

    def run_pi(self, child_calls: list[dict[str, object]], *flags: str, agent: str = "cad-clerk", marker: str = "You are a CAD clerk") -> list[dict[str, object]]:
        script = self.root / "script.json"
        script.write_text(json.dumps({
            "childMarker": marker,
            "parent": [
                {"name": "bash", "arguments": {"command": f"touch {self.parent_mark}"}},
                {"name": "subagent", "arguments": {"agent": agent, "task": "Run the scripted chore.", "async": False}},
            ],
            "child": child_calls,
        }), encoding="utf-8")
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.home), "TMPDIR": str(self.tmp),
            "PI_OFFLINE": "1", "CAD_PI_SCRIPT": str(script), "CAD_PI_LOG": str(self.log),
        }
        command = [str(PI), "-p", "--no-session", "--offline", "-ne", "-e", str(SUBAGENTS), "-e", str(INTEGRATION / "scripted-model.js"),
                   "--provider", "cadtest", "--model", "scripted", *flags, "Run the CAD chore."]
        done = subprocess.run(command, capture_output=True, text=True, env=env, cwd=self.project, timeout=300)
        self.output = done.stdout + done.stderr
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def child_results(self, entries: list[dict[str, object]]) -> list[dict[str, object]]:
        return [entry for entry in entries if entry["who"] == "child"]

    def uv_calls(self) -> list[str]:
        return self.uv_log.read_text(encoding="utf-8").splitlines() if self.uv_log.exists() else []

    CHILD = [
        {"name": "bash", "arguments": {"command": "touch CHILD_MARK"}},
        {"name": "cad_run", "arguments": {"launcher": "cad-flow/scripts/scratch.py", "args": ["init"]}},
        {"name": "cad_run", "arguments": {"launcher": BOM, "args": ["asm.step", "--hw", "hw", "--out", ".cad-work/u1/bom.csv"]}},
        {"name": "write", "arguments": {"path": "SKILL_FILE", "content": "print('owned')\n"}},
        {"name": "write", "arguments": {"path": "hw/product.yaml", "content": "x\n"}},
        {"name": "write", "arguments": {"path": ".cad-work/u1/draft.md", "content": "draft\n"}},
    ]

    def child_calls(self, scope: Path) -> list[dict[str, object]]:
        skill_file = str(scope / "skills" / BOM)
        calls = json.loads(json.dumps(self.CHILD).replace("CHILD_MARK", str(self.child_mark)).replace("SKILL_FILE", skill_file))
        return calls  # type: ignore[no-any-return]

    def assert_confined_child(self, scope: Path, *flags: str) -> None:
        skill_file = scope / "skills" / BOM
        before = skill_file.read_bytes()
        entries = self.run_pi(self.child_calls(scope), *flags)
        child = self.child_results(entries)
        self.assertTrue(self.parent_mark.exists(), "the parent session keeps its own bash\n" + self.output)
        self.assertEqual([entry["tool"] for entry in child], ["bash", "cad_run", "cad_run", "write", "write", "write"], self.output)
        bash, other, bom, skill_write, record_write, scratch_write = child
        self.assertTrue(bash["isError"], bash)
        self.assertFalse(self.child_mark.exists())
        self.assertTrue(other["isError"], other)
        self.assertFalse(bom["isError"], bom)
        self.assertTrue(str(bom["text"]).startswith("exit 0"), bom)
        self.assertEqual(self.uv_calls(), [f"run --script {(scope / 'skills/cad-flow/scripts/run.py').resolve()} {skill_file.resolve()} asm.step --hw hw --out .cad-work/u1/bom.csv",
                                           str(self.project)])
        self.assertTrue(skill_write["isError"], skill_write)
        self.assertEqual(skill_file.read_bytes(), before)
        self.assertTrue(record_write["isError"], record_write)
        self.assertFalse((self.project / "hw/product.yaml").exists())
        self.assertFalse(scratch_write["isError"], scratch_write)
        self.assertEqual((self.project / ".cad-work/u1/draft.md").read_text(encoding="utf-8"), "draft\n")

    def test_user_scope_child_runs_only_cad_run_and_writes_only_scratch(self) -> None:
        self.assert_confined_child(self.install("--user"))

    def test_project_scope_child_runs_only_cad_run_and_writes_only_scratch(self) -> None:
        self.assert_confined_child(self.install("--project", str(self.project)), "--approve")

    def test_a_unicode_space_in_a_path_cannot_reach_a_link_out_of_scratch(self) -> None:
        # pi turns U+00A0 into a plain space before it opens the path, so "a<NBSP>b" is the linked directory "a b".
        scope = self.install("--user")
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / ".cad-work/u1/a b").symlink_to(outside)
        calls = [{"name": "write", "arguments": {"path": ".cad-work/u1/a\u00a0b/escaped.txt", "content": "evil"}},
                 {"name": "write", "arguments": {"path": ".cad-work/u1/kept.txt", "content": "ok"}}]
        entries = self.run_pi(calls)
        escaped, kept = self.child_results(entries)
        self.assertTrue(escaped["isError"], self.output)
        self.assertEqual(list(outside.iterdir()), [], self.output)
        self.assertFalse(kept["isError"], kept)
        self.assertEqual((self.project / ".cad-work/u1/kept.txt").read_text(encoding="utf-8"), "ok")
        self.assertTrue((scope / "cad-guard/cad-guard.js").is_file())

    def test_edit_changes_a_scratch_file_and_refuses_a_record_file(self) -> None:
        # The guard replaces pi's edit tool, so its wiring is checked under the real tool.
        self.install("--user")
        (self.project / ".cad-work/u1/note.md").write_text("one two\n", encoding="utf-8")
        (self.project / "hw/product.yaml").write_text("one two\n", encoding="utf-8")
        change = {"edits": [{"oldText": "two", "newText": "2"}]}
        entries = self.run_pi([{"name": "edit", "arguments": {"path": ".cad-work/u1/note.md", **change}},
                               {"name": "edit", "arguments": {"path": "hw/product.yaml", **change}}])
        scratch, record = self.child_results(entries)
        self.assertFalse(scratch["isError"], self.output)
        self.assertEqual((self.project / ".cad-work/u1/note.md").read_text(encoding="utf-8"), "one 2\n")
        self.assertTrue(record["isError"], self.output)
        self.assertEqual((self.project / "hw/product.yaml").read_text(encoding="utf-8"), "one two\n")

    def install_web_access(self) -> None:
        """Stand in for `pi install npm:pi-web-access`, which keeps the package under the scope's `npm/node_modules`."""
        modules = self.home / ".pi/agent/npm/node_modules"
        modules.mkdir(parents=True)
        (modules / "pi-web-access").symlink_to(WEB_ACCESS)

    def test_the_sourcing_researcher_child_has_the_web_tools(self) -> None:
        # A foreground child loads no ambient extension, so the agent must name pi-web-access itself.
        self.install("--user")
        self.install_web_access()
        entries = self.run_pi([{"name": "fetch_content", "arguments": {"url": "http://127.0.0.1:9/"}}],
                              agent="cad-sourcing-researcher", marker="You are a read-only sourcing researcher")
        self.assertEqual([entry["tool"] for entry in self.child_results(entries)], ["fetch_content"], self.output)
        (fetch,) = self.child_results(entries)
        self.assertNotIn("not found", str(fetch["text"]).lower(), self.output)
        self.assertNotIn("unknown tool", str(fetch["text"]).lower(), self.output)

    def test_a_child_whose_guard_is_missing_runs_nothing(self) -> None:
        scope = self.install("--user")
        shutil.rmtree(scope / "cad-guard")
        self.assert_runs_nothing(scope)

    def test_a_child_whose_guard_is_present_but_fails_to_import_runs_nothing(self) -> None:
        scope = self.install("--user")
        (scope / "cad-guard/cad-guard.js").write_text('import "./no-such-module.js";\nexport const cadGuard = () => {};\n', encoding="utf-8")
        self.assertTrue((scope / "cad-guard/cad-clerk.js").is_file())
        self.assert_runs_nothing(scope)

    def assert_runs_nothing(self, scope: Path) -> None:
        skill_file = scope / "skills" / BOM
        before = skill_file.read_bytes()
        entries = self.run_pi(self.child_calls(scope))
        launch = [entry for entry in entries if entry["who"] == "parent" and entry["tool"] == "subagent"]
        self.assertEqual(len(launch), 1, self.output)
        self.assertTrue(launch[0]["isError"], launch[0])
        self.assertIn("Agent 'cad-clerk' ran as a foreground child", str(launch[0]["text"]))
        self.assertIn("these child tools were unavailable: cad_run", str(launch[0]["text"]))
        self.assertIn("(failed)", str(launch[0]["text"]))
        self.assertEqual(self.child_results(entries), [], self.output)
        self.assertEqual(skill_file.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in (self.project / ".cad-work/u1").iterdir()), [])
        self.assertTrue(self.parent_mark.exists(), self.output)
        self.assertFalse(self.child_mark.exists(), self.output)
        self.assertEqual(self.uv_calls(), [])
        self.assertFalse((self.project / "hw/product.yaml").exists(), self.output)


if __name__ == "__main__":
    unittest.main()
