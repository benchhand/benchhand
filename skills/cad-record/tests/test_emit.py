#!/usr/bin/env python3
"""cad-record emit.py: output modes, the compact summary and the full-JSON file.

Run: python3 -m unittest discover -s skills/cad-record/tests -p test_emit.py -v
Standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_module  # noqa: E402

emit = load_module("cad_record_emit", SKILLS / "cad-record" / "scripts" / "emit.py")
RUN_PY = SKILLS / "cad-flow" / "scripts" / "run.py"
CAD_MODEL = SKILLS / "cad-model" / "scripts" / "cad_model.py"


def emitted(result: object, **kwargs: object) -> str:
    stream = StringIO()
    emit.emit(result, stream=stream, **kwargs)  # type: ignore[arg-type]
    return stream.getvalue()


class EmitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.result = {"status": "fail", "label": "v3", "blocking_failures": [f"rule-{n}" + "-" * 60 for n in range(8)]}
        self.text = json.dumps(self.result, indent=2)

    def agent(self, **kwargs: object) -> str:
        with mock.patch.dict(os.environ, {emit.ENV: "compact"}):
            return emitted(self.result, tool="tool", name="tool", code=1, **kwargs)

    def test_plain_mode_prints_the_indented_json_and_nothing_else(self) -> None:
        with mock.patch.dict(os.environ, {emit.ENV: "json"}, clear=True):
            self.assertEqual(emitted(self.result, tool="tool", name="tool", code=1, scratch=self.root),
                             self.text + "\n")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_the_json_flag_beats_the_environment_which_beats_the_default(self) -> None:
        cases = [(True, "compact", True, "json"), (False, "compact", False, "compact"),
                 (False, "json", True, "json"), (False, "text", False, "compact"),
                 (False, "bogus", True, "compact"), (False, "bogus", False, "json"),
                 (False, None, True, "compact"), (False, None, False, "json")]
        for flag, value, tty, expected in cases:
            with self.subTest(flag=flag, env=value, tty=tty):
                env = {} if value is None else {emit.ENV: value}
                self.assertEqual(emit.mode(flag, env, tty), expected)

    def printed_without_env(self, tty: bool) -> str:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(emit, "stdout_isatty", return_value=tty):
            return emitted(self.result, tool="tool", name="tool", code=1, scratch=self.root)

    def test_a_terminal_with_no_flag_or_env_gets_the_compact_summary(self) -> None:
        shown = self.printed_without_env(True)
        self.assertTrue(shown.startswith("tool: fail (exit 1)"))
        self.assertIn("full JSON: ", shown)

    def test_a_pipe_with_no_flag_or_env_gets_the_full_json_byte_for_byte(self) -> None:
        self.assertEqual(self.printed_without_env(False), self.text + "\n")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_the_default_follows_the_real_stdout(self) -> None:
        with mock.patch.object(sys.stdout, "isatty", return_value=True, create=True):
            self.assertTrue(emit.stdout_isatty())

    def test_the_json_flag_prints_full_json_in_compact_mode(self) -> None:
        with mock.patch.dict(os.environ, {emit.ENV: "compact"}):
            shown = emitted(self.result, tool="tool", name="tool", code=1, scratch=self.root, json_flag=True)
        self.assertEqual(shown, self.text + "\n")

    def test_the_validation_hook_sees_the_result_and_schema_id_before_anything_prints(self) -> None:
        with mock.patch.object(emit, "validate_result") as hook:
            emitted(self.result, tool="tool", name="tool", code=1, schema_id="tool-result/1")
        hook.assert_called_once_with(self.result, "tool-result/1")

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
        self.assertLess(max(map(len, shown.splitlines())), emit.MAX_LINE + 10)

    def test_findings_block_unless_flagged_or_a_warning(self) -> None:
        findings = [{"code": "a", "severity": "error"}, {"code": "b", "severity": "warning"},
                    {"code": "c", "blocking": False}, {"code": "d", "blocking": True}, {"code": "e"}]
        self.assertEqual([f["code"] for f in emit.blocking_of({"findings": findings})], ["a", "d", "e"])

    def test_a_finding_is_described_by_identity_subject_and_message(self) -> None:
        self.assertEqual(emit.describe({"check": "min_wall", "part": "P-1", "message": "thin", "x": 1}),
                         "min_wall: P-1: thin")

    def test_verdict_falls_back_to_ok(self) -> None:
        self.assertEqual([emit.verdict_of(r) for r in ({"ok": True}, {"ok": False}, {"status": "warn"}, {})],
                         ["pass", "fail", "warn", "unknown"])

    def test_json_env_requests_json_and_keeps_the_rest(self) -> None:
        self.assertEqual(emit.json_env({emit.ENV: "compact", "A": "b"}), {emit.ENV: "json", "A": "b"})


class RunPyTests(unittest.TestCase):
    def load_run(self):
        spec = importlib.util.spec_from_file_location("cad_run_for_emit", RUN_PY)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def launched_env(self, env: dict[str, str]) -> str | None:
        run = self.load_run()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("os.execvp") as execvp:
            run.main([str(CAD_MODEL)])
            self.assertTrue(execvp.called)
            return os.environ.get(emit.ENV)

    def test_run_py_switches_compact_output_on(self) -> None:
        self.assertEqual(self.launched_env({"PATH": os.environ["PATH"]}), "compact")

    def test_run_py_keeps_a_value_the_caller_set(self) -> None:
        self.assertEqual(self.launched_env({"PATH": os.environ["PATH"], emit.ENV: "json"}), "json")


if __name__ == "__main__":
    unittest.main()
