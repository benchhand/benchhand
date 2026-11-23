#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for the pi CAD extension: cad_run runs only listed skill scripts, and writes stay in .cad-work.

The extension runs under `node`. Without it these tests skip, and CAD_REQUIRE_PI=1 (set in CI)
turns the skip into a failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

GUARD = Path(__file__).resolve().parent / "pi/guard/cad-guard.js"
NODE = shutil.which("node")
REQUIRED = os.environ.get("CAD_REQUIRE_PI") == "1"
BOM = "cad-bom/scripts/bom.py"
RUN_PY = "cad-flow/scripts/run.py"
FAKE_UV = """#!/bin/sh
exec python3 - "$@" <<'EOF'
import json, os, sys
with open({log!r}, "w", encoding="utf-8") as stream:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}}, stream)
print("fake stdout")
print("fake stderr", file=sys.stderr)
sys.exit(3)
EOF
"""


def setUpModule() -> None:
    if NODE is None and REQUIRED:
        raise AssertionError("CAD_REQUIRE_PI=1 but node is not on PATH")


class Scope:
    """An installed pi scope: `skills/` beside `cad-guard/`, plus a project with `.cad-work/` and an outside dir."""

    def __enter__(self) -> "Scope":
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.root = root
        self.skills = root / "scope/skills"
        self.guard_dir = root / "scope/cad-guard"
        self.project = root / "project"
        self.outside = root / "outside"
        for path in (self.skills / RUN_PY, self.skills / BOM, self.skills / "cad-bom/scripts/other.py", self.outside / "evil.py"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        (self.project / ".cad-work/u1").mkdir(parents=True)
        (self.project / "hw").mkdir()
        self.guard_dir.mkdir()
        self.module = self.guard_dir / "cad-guard.js"
        shutil.copy(GUARD, self.module)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._tmp.cleanup()

    def node(self, body: str, payload: object = None, env: dict[str, str] | None = None) -> object:
        """Run `body` with the copied module imported as `g` and the JSON payload as `input`; return its printed JSON."""
        assert NODE is not None
        script = (
            f"import * as g from {json.dumps(self.module.as_uri())};"
            "const input = JSON.parse(process.argv[1]);"
            f"const out = await (async () => {{ {body} }})();"
            "console.log(JSON.stringify(out ?? null));"
        )
        done = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(payload)], capture_output=True, text=True,
                              timeout=60, env=env if env is not None else dict(os.environ), cwd=self.project)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        return json.loads(done.stdout)

    def resolve(self, launcher: str, launchers: list[str]) -> object:
        return self.node("const r = g.resolveLauncher(input.launcher, input.launchers, input.skills);"
                         "return r instanceof Error ? {error: r.message} : r;",
                         {"launcher": launcher, "launchers": launchers, "skills": str(self.skills)})

    def decide(self, tool: str, tool_input: object) -> object:
        return self.node("return g.toolCallDecision({toolName: input.tool, input: input.input}, {cwd: input.cwd, skills: input.skills, guardDir: input.guard});",
                         {"tool": tool, "input": tool_input, "cwd": str(self.project), "skills": str(self.skills), "guard": str(self.guard_dir)})


@unittest.skipUnless(NODE or REQUIRED, "node is needed to run the pi extension")
class LauncherTests(unittest.TestCase):
    def test_a_listed_launcher_resolves_to_the_installed_files(self) -> None:
        with Scope() as scope:
            self.assertEqual(scope.resolve(BOM, [BOM]), {"runPy": str(scope.skills / RUN_PY), "target": str(scope.skills / BOM)})

    def test_refuses_a_launcher_the_agent_does_not_list(self) -> None:
        with Scope() as scope:
            for launcher in ("cad-bom/scripts/other.py", "", "x"):
                with self.subTest(launcher=launcher):
                    self.assertIn("refuses launcher", scope.resolve(launcher, [BOM])["error"])  # type: ignore[index]

    def test_refuses_a_listed_launcher_that_leaves_the_plain_path(self) -> None:
        with Scope() as scope:
            for launcher in ("cad-bom/scripts/../scripts/bom.py", "/etc/passwd", "cad-bom/scripts/missing.py"):
                with self.subTest(launcher=launcher):
                    self.assertIn("not a plain installed file", scope.resolve(launcher, [launcher])["error"])  # type: ignore[index]

    def test_refuses_a_launcher_or_run_py_replaced_by_a_symlink(self) -> None:
        for name in (BOM, RUN_PY):
            with self.subTest(file=name), Scope() as scope:
                (scope.skills / name).unlink()
                (scope.skills / name).symlink_to(scope.outside / "evil.py")
                self.assertIn("not a plain installed file", scope.resolve(BOM, [BOM])["error"])  # type: ignore[index]

    def test_a_symlinked_skills_dir_resolves_to_its_real_files(self) -> None:
        with Scope() as scope:
            link = scope.root / "linked-skills"
            link.symlink_to(scope.skills)
            result = scope.node("return g.resolveLauncher(input.l, [input.l], input.s);", {"l": BOM, "s": str(link)})
            self.assertEqual(result, {"runPy": str(scope.skills / RUN_PY), "target": str(scope.skills / BOM)})

    def test_the_command_runs_run_py_with_the_target_and_the_args_as_given(self) -> None:
        with Scope() as scope:
            argv = scope.node("return g.command({runPy: 'R', target: 'T'}, input);", ["--out", "a b", "$(id)", "; rm -rf /"])
        self.assertEqual(argv, ["run", "--script", "R", "T", "--out", "a b", "$(id)", "; rm -rf /"])

    def test_the_child_environment_keeps_only_fixed_names(self) -> None:
        parent = {"PATH": "/bin", "HOME": "/h", "FREECADCMD": "/f", "UV_INDEX_URL": "http://evil", "PYTHONPATH": "/evil",
                  "UV_PYTHON": "/evil/python", "CAD_SKILLS": "/evil", "LD_PRELOAD": "/evil.so"}
        with Scope() as scope:
            env = scope.node("return g.childEnv(input, '/skills');", parent)
        self.assertEqual(env, {"PATH": "/bin", "HOME": "/h", "FREECADCMD": "/f", "CAD_SKILLS": "/skills"})


@unittest.skipUnless(NODE or REQUIRED, "node is needed to run the pi extension")
class ToolCallTests(unittest.TestCase):
    def test_blocks_every_shell_call(self) -> None:
        with Scope() as scope:
            for tool, tool_input in (("bash", {"command": "uv run --script x"}), ("bash", {}), ("powershell", {"command": "dir"})):
                with self.subTest(tool=tool, input=tool_input):
                    self.assertEqual(scope.decide(tool, tool_input)["block"], True)  # type: ignore[index]

    def test_allows_writes_inside_the_project_work_dir(self) -> None:
        with Scope() as scope:
            for tool, path in (("write", ".cad-work/u1/draft.md"), ("edit", ".cad-work/u1/new/deep.txt"), ("write", "@.cad-work/u1/x"),
                               ("write", str(scope.project / ".cad-work/u1/abs.txt"))):
                with self.subTest(tool=tool, path=path):
                    self.assertIsNone(scope.decide(tool, {"path": path}))

    def test_blocks_writes_outside_the_work_dir_or_into_installed_files(self) -> None:
        with Scope() as scope:
            (scope.project / ".cad-work/to-skills").symlink_to(scope.skills)
            (scope.project / ".cad-work/to-outside").symlink_to(scope.outside)
            (scope.project / ".cad-work/u1/sub").mkdir()
            (scope.project / ".cad-work/u1/sub-link").symlink_to(scope.outside)
            cases = {
                "record": "hw/product.yaml",
                "project root": "notes.md",
                "work dir itself": ".cad-work",
                "skills file": str(scope.skills / BOM),
                "guard file": str(scope.module),
                "via symlink into skills": ".cad-work/to-skills/cad-bom/scripts/bom.py",
                "via symlink outside": ".cad-work/to-outside/x.py",
                "dot-dot after symlink": ".cad-work/u1/sub-link/../../../hw/x",
                "dot-dot inside": ".cad-work/u1/sub/../x",
                "home": "~/x",
                "outside absolute": str(scope.outside / "x"),
                "empty": "",
            }
            for name, path in cases.items():
                with self.subTest(case=name):
                    self.assertEqual(scope.decide("write", {"path": path})["block"], True)  # type: ignore[index]
            self.assertEqual(scope.decide("edit", {})["block"], True)  # type: ignore[index]

    def test_lets_other_tools_through(self) -> None:
        with Scope() as scope:
            for tool in ("read", "grep", "cad_run"):
                with self.subTest(tool=tool):
                    self.assertIsNone(scope.decide(tool, {"path": "/etc/passwd"}))


@unittest.skipUnless(NODE or REQUIRED, "node is needed to run the pi extension")
class CadRunToolTests(unittest.TestCase):
    """The registered tool, driven through a stand-in `pi` object and a fake `uv` on PATH."""

    BODY = (
        "const tools = {}; const hooks = {};"
        "g.cadGuard('cad-clerk', input.launchers, {guardDir: input.guard})({registerTool: (t) => { tools[t.name] = t; }, on: (n, f) => { hooks[n] = f; }});"
        "const tool = tools.cad_run;"
        "try { const r = await tool.execute('id', input.params, undefined, undefined, {cwd: input.cwd});"
        "  return {schema: tool.parameters, result: r, hooks: Object.keys(hooks)}; }"
        "catch (e) { return {schema: tool.parameters, error: e.message}; }"
    )

    def call(self, scope: Scope, params: object) -> dict[str, object]:
        bin_dir = scope.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        uv = bin_dir / "uv"
        self.log = scope.root / "uv.json"
        uv.write_text(FAKE_UV.format(log=str(self.log)), encoding="utf-8")
        uv.chmod(0o755)
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(scope.root), "UV_INDEX_URL": "http://evil", "PYTHONPATH": "/evil"}
        payload = {"launchers": [BOM], "guard": str(scope.guard_dir), "cwd": str(scope.project), "params": params}
        return scope.node(self.BODY, payload, env=env)  # type: ignore[return-value]

    def test_runs_uv_without_a_shell_from_the_project_root(self) -> None:
        with Scope() as scope:
            out = self.call(scope, {"launcher": BOM, "args": ["a.step", "--out", ".cad-work/u1/bom.csv", "$(id)"]})
            seen = json.loads(self.log.read_text(encoding="utf-8"))
            self.assertEqual(seen["argv"], ["run", "--script", str(scope.skills / RUN_PY), str(scope.skills / BOM), "a.step", "--out", ".cad-work/u1/bom.csv", "$(id)"])
            self.assertEqual(Path(seen["cwd"]).resolve(), scope.project)
            self.assertEqual(seen["env"]["CAD_SKILLS"], str(scope.skills))
            self.assertEqual(seen["env"].keys() & {"UV_INDEX_URL", "PYTHONPATH"}, set())
        result = out["result"]
        self.assertEqual(result["details"], {"launcher": BOM, "exitCode": 3})  # type: ignore[index]
        text = result["content"][0]["text"]  # type: ignore[index]
        self.assertTrue(text.startswith("exit 3\n"), text)
        self.assertIn("fake stdout", text)
        self.assertIn("fake stderr", text)
        self.assertEqual(out["hooks"], ["tool_call"])

    def test_the_schema_offers_only_the_agent_launchers(self) -> None:
        with Scope() as scope:
            out = self.call(scope, {"launcher": "cad-bom/scripts/other.py", "args": []})
        self.assertEqual(out["schema"]["properties"]["launcher"]["enum"], [BOM])  # type: ignore[index]
        self.assertIn("refuses launcher", out["error"])
        self.assertFalse(self.log.exists())

    def test_refuses_args_that_are_not_strings(self) -> None:
        with Scope() as scope:
            for args in ("a b", [1], None):
                with self.subTest(args=args):
                    self.assertIn("args must be a list of strings", self.call(scope, {"launcher": BOM, "args": args})["error"])  # type: ignore[operator]
            self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
