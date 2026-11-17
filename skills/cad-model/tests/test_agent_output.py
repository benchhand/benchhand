#!/usr/bin/env python3
"""cadmodel.agent_output and the producers that print through it.

Run: python3 -m unittest discover -s skills/cad-model/tests -p test_agent_output.py -v
The emitter tests need only the standard library. The lamp-arm tests build the example once with cad_model.py
(CadQuery, `uv`), then run each switched producer in both modes.

Byte budgets (stdout of one command in agent mode, lamp-arm example, v1 built from examples/lamp-arm/hw):

    command               before (full JSON)   after (agent summary)   budget
    cad_model run                 5534                  176                  400
    cad_model check              97003                  178                  400
    cad_model latest               174                  174 (printed whole)  250
    form_check --version         12563                  430                  800
    validate                       700                  385                  700

The "after" column includes a scratch path of about 100 bytes; a shorter path shortens it.

Raise a budget only with a reason in the commit message: each byte is read by an agent on every call.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from cadmodel import agent_output  # noqa: E402 - stdlib-only

LAMP_HW = SKILLS.parent / "examples" / "lamp-arm" / "hw"
CAD_MODEL = SCRIPTS / "cad_model.py"
FORM_CHECK = SKILLS / "cad-design" / "scripts" / "form_check.py"
VALIDATE = SKILLS / "cad-record" / "scripts" / "validate.py"
RUN_PY = SKILLS / "cad-flow" / "scripts" / "run.py"

PLAIN_ENV = {k: v for k, v in os.environ.items() if k != agent_output.ENV}
AGENT_ENV = {**PLAIN_ENV, agent_output.ENV: "1"}

# Agent-mode stdout bytes per command on the lamp-arm example; see the table in the module docstring.
BUDGETS = {"run": 400, "check": 400, "latest": 250, "form_check": 800, "validate": 700}


def emitted(result: object, **kwargs: object) -> str:
    stream = StringIO()
    agent_output.emit(result, stream=stream, **kwargs)  # type: ignore[arg-type]
    return stream.getvalue()


def without_timings(value: object) -> object:
    """`value` without the wall-clock fields, which differ between two runs of one command."""
    if isinstance(value, dict):
        return {k: without_timings(v) for k, v in value.items() if k not in ("timings_s", "seconds", "wall_s")}
    if isinstance(value, list):
        return [without_timings(v) for v in value]
    return value


class EmitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.result = {"status": "fail", "label": "v3", "blocking_failures": [f"rule-{n}" + "-" * 60 for n in range(8)]}
        self.text = json.dumps(self.result, indent=2)

    def agent(self, **kwargs: object) -> str:
        with mock.patch.dict(os.environ, {agent_output.ENV: "1"}):
            return emitted(self.result, tool="tool", name="tool", code=1, **kwargs)

    def test_plain_mode_prints_the_indented_json_and_nothing_else(self) -> None:
        with mock.patch.dict(os.environ, PLAIN_ENV, clear=True):
            self.assertEqual(emitted(self.result, tool="tool", name="tool", code=1, scratch=self.root),
                             self.text + "\n")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_zero_value_keeps_plain_mode(self) -> None:
        self.assertFalse(agent_output.enabled({agent_output.ENV: "0"}))
        self.assertFalse(agent_output.enabled({}))
        self.assertTrue(agent_output.enabled({agent_output.ENV: "1"}))

    def test_the_summary_names_verdict_exit_five_blockers_the_rest_and_the_full_file(self) -> None:
        out = self.agent(scratch=self.root, facts={"label": "v3", "empty": None})
        full = next(self.root.glob("tool-*.json"))
        self.assertEqual(out, "\n".join([
            "tool: fail (exit 1)", "label: v3", "blocking (8):",
            *(f"  - rule-{n}" + "-" * 60 for n in range(5)), "  +3 more", f"full JSON: {full.resolve()}"]) + "\n")

    def test_a_result_no_longer_than_its_summary_is_printed_in_full(self) -> None:
        self.result = {"status": "pass", "label": "v1"}
        self.assertEqual(self.agent(scratch=self.root), json.dumps(self.result, indent=2) + "\n")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_the_full_file_holds_exactly_what_plain_mode_prints(self) -> None:
        self.agent(scratch=self.root)
        self.assertEqual(next(self.root.glob("tool-*.json")).read_text(encoding="utf-8"), self.text + "\n")

    def test_an_out_file_that_holds_the_result_is_the_named_path_and_nothing_is_copied(self) -> None:
        out = self.root / "report.json"
        out.write_text(self.text + "\n", encoding="utf-8")
        self.assertIn(f"full JSON: {out.resolve()}", self.agent(scratch=self.root / "scratch", out=out))
        self.assertFalse((self.root / "scratch").exists())

    def test_an_out_file_with_other_content_is_not_the_full_json(self) -> None:
        out = self.root / "packet.json"
        out.write_text("{}\n", encoding="utf-8")
        shown = self.agent(scratch=self.root / "scratch", out=out)
        self.assertNotIn(str(out.resolve()), shown)
        self.assertIn(str((self.root / "scratch").resolve()), shown)

    def test_without_a_place_to_write_the_full_json_is_printed(self) -> None:
        self.assertEqual(self.agent(), self.text + "\n")

    def test_an_unwritable_scratch_prints_the_full_json(self) -> None:
        blocker = self.root / "file"
        blocker.write_text("x", encoding="utf-8")
        self.assertEqual(self.agent(scratch=blocker / "below"), self.text + "\n")

    def test_a_long_line_is_clipped(self) -> None:
        self.result = {"status": "fail", "blocking_failures": ["x" * 5000]}
        shown = self.agent(scratch=self.root)
        self.assertLess(max(map(len, shown.splitlines())), agent_output.MAX_LINE + 10)

    def test_findings_block_unless_flagged_or_a_warning(self) -> None:
        findings = [{"code": "a", "severity": "error"}, {"code": "b", "severity": "warning"},
                    {"code": "c", "blocking": False}, {"code": "d", "blocking": True}, {"code": "e"}]
        self.assertEqual([f["code"] for f in agent_output.blocking_of({"findings": findings})], ["a", "d", "e"])

    def test_a_finding_is_described_by_identity_subject_and_message(self) -> None:
        self.assertEqual(agent_output.describe({"check": "min_wall", "part": "P-1", "message": "thin", "x": 1}),
                         "min_wall: P-1: thin")

    def test_verdict_falls_back_to_ok(self) -> None:
        self.assertEqual([agent_output.verdict_of(r) for r in ({"ok": True}, {"ok": False}, {"status": "warn"}, {})],
                         ["pass", "fail", "warn", "unknown"])

    def test_child_env_drops_only_the_switch(self) -> None:
        env = agent_output.child_env({agent_output.ENV: "1", "A": "b"})
        self.assertEqual(env, {"A": "b"})


class RunPyTests(unittest.TestCase):
    def load_run(self):
        spec = importlib.util.spec_from_file_location("cad_run_for_agent_output", RUN_PY)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def launched_env(self, env: dict[str, str]) -> str | None:
        run = self.load_run()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("os.execvp") as execvp:
            run.main([str(SCRIPTS / "cad_model.py")])
            self.assertTrue(execvp.called)
            return os.environ.get(agent_output.ENV)

    def test_run_py_switches_agent_output_on(self) -> None:
        self.assertEqual(self.launched_env({"PATH": os.environ["PATH"]}), "1")

    def test_run_py_keeps_a_value_the_caller_set(self) -> None:
        self.assertEqual(self.launched_env({"PATH": os.environ["PATH"], agent_output.ENV: "0"}), "0")


@unittest.skipUnless(shutil.which("uv"), "uv is required to run the producers")
class LampArmTests(unittest.TestCase):
    """Each switched producer on the lamp-arm example: summary, full file and unchanged plain output."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hw = Path(cls.tmp.name) / "hw"
        shutil.copytree(LAMP_HW, cls.hw)
        cls.run_result = cls.tool(CAD_MODEL, "run", str(cls.hw), "--label", "v1", env=PLAIN_ENV)
        assert cls.run_result.returncode == 0, cls.run_result.stderr[-3000:]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    @staticmethod
    def tool(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # no timeout: a cold uv cache builds CadQuery's environment first
        return subprocess.run(["uv", "run", "--quiet", "--script", str(script), *args], capture_output=True,
                              text=True, env=env, check=False)

    def commands(self) -> dict[str, tuple[Path, tuple[str, ...]]]:
        hw = str(self.hw)
        return {"check": (CAD_MODEL, ("check", "v1", "--hw", hw, "--no-cache")),
                "latest": (CAD_MODEL, ("latest", hw, "--label", "v1")),
                "form_check": (FORM_CHECK, ("--hw", hw, "--version", "v1", "--out", str(self.hw.parent / ".cad-work" / "p.json"))),
                "validate": (VALIDATE, (hw,))}

    def both(self, name: str) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
        script, args = self.commands()[name]
        return self.tool(script, *args, env=PLAIN_ENV), self.tool(script, *args, env=AGENT_ENV)

    def test_every_producer_names_a_full_json_file_equal_to_its_plain_stdout_with_the_same_exit_code(self) -> None:
        for name in self.commands():
            with self.subTest(command=name):
                plain, agent = self.both(name)
                self.assertEqual(agent.returncode, plain.returncode, agent.stderr[-2000:])
                last = agent.stdout.rstrip("\n").splitlines()[-1]
                # a result shorter than its summary (latest) is printed in full
                full = (Path(last.removeprefix("full JSON: ")).read_text(encoding="utf-8")
                        if last.startswith("full JSON: ") else agent.stdout)
                self.assertEqual(without_timings(json.loads(full)), without_timings(json.loads(plain.stdout)))
                self.assertLessEqual(len(agent.stdout.encode()), BUDGETS[name], agent.stdout)
                self.assertLessEqual(len(agent.stdout), len(plain.stdout) + 1)

    def test_cad_model_run_summary_stays_inside_its_budget(self) -> None:
        other = Path(self.tmp.name) / "again"
        shutil.copytree(LAMP_HW, other / "hw")
        agent = self.tool(CAD_MODEL, "run", str(other / "hw"), "--label", "v1", env=AGENT_ENV)
        self.assertEqual(agent.returncode, 0, agent.stderr[-2000:])
        self.assertLessEqual(len(agent.stdout.encode()), BUDGETS["run"], agent.stdout)
        path = Path(agent.stdout.rstrip("\n").splitlines()[-1].removeprefix("full JSON: "))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["label"], "v1")

    def test_cad_model_check_out_file_is_the_named_full_json(self) -> None:
        out = Path(self.tmp.name) / ".cad-work" / "check.json"
        out.parent.mkdir(exist_ok=True)
        agent = self.tool(CAD_MODEL, "check", "v1", "--hw", str(self.hw), "--out", str(out), env=AGENT_ENV)
        self.assertIn(f"full JSON: {out.resolve()}", agent.stdout)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["label"], "v1")


if __name__ == "__main__":
    unittest.main()
