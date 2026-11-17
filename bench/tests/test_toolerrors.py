import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import toolerrors  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
# The recorded probe ran in /scratch/bench-probe/proj (paths scrubbed from the run's temp directory).
SCRATCH = Path("/scratch/bench-probe")
PROJECT, HOME = SCRATCH / "proj", SCRATCH / "home"
STAMP = (f"cd {PROJECT} && uv run --script {HOME}/.config/opencode/skills/cad-flow/scripts/run.py "
         "cad-recheck/scripts/stamp.py --verify")


def recorded_export() -> dict:
    return json.loads((DATA / "export-shell-question.json").read_text(encoding="utf-8"))


def recorded_events() -> list[dict]:
    return [json.loads(line) for line in (DATA / "events-shell-question.jsonl").read_text(encoding="utf-8").splitlines()]


def shell_part(export: dict) -> dict:
    return next(part for message in export["messages"] if message["type"] == "assistant"
                for part in message["content"] if part.get("name") == "shell")


def with_command(export: dict, command: str, output: str, code: int) -> dict:
    export = copy.deepcopy(export)
    state = shell_part(export)["state"]
    state["input"]["command"] = command
    state["content"] = [{"type": "text", "text": output}, {"type": "text", "text": f"Exit code: {code}"}]
    state["metadata"]["exit"] = code
    return export


class CollectTests(unittest.TestCase):
    def test_the_recorded_failing_echo_is_not_a_repo_tool_and_is_not_kept(self) -> None:
        # The recorded call is `echo hi; exit 1`: it exits 1 but runs no record or check tool.
        self.assertEqual(toolerrors.collect([recorded_export()], PROJECT, SCRATCH, HOME), [])

    def test_a_failed_stamp_verify_keeps_its_relative_argv_exit_code_and_output_tail(self) -> None:
        output = (f"stamp.py: the staged tree {PROJECT}/hw is at stage parts, which has no passing check result\n"
                  "digest 3f2b9c1e8d7a4b6c9e0f1a2b3c4d5e6f3f2b9c1e8d7a4b6c9e0f1a2b3c4d5e6f")
        export = with_command(recorded_export(), STAMP, output, 2)
        [record] = toolerrors.collect([export], PROJECT, SCRATCH, HOME)
        self.assertEqual(record["argv"], ["cd", ".", "&&", "uv", "run", "--script", "$CAD_SKILLS/cad-flow/scripts/run.py",
                                          "cad-recheck/scripts/stamp.py", "--verify"])
        self.assertEqual(record["exit_code"], 2)
        self.assertEqual(record["output_tail"], output.replace(f"{PROJECT}/", ""))
        self.assertEqual(record["session"], export["info"]["id"])

    def test_the_event_stream_shape_gives_the_same_record(self) -> None:
        events = copy.deepcopy(recorded_events())
        part = next(event["part"] for event in events if event["type"] == "tool_use" and event["part"]["tool"] == "shell")
        part["state"]["input"]["command"] = STAMP
        part["state"]["metadata"]["metadata"]["exit"] = 2
        part["state"]["metadata"]["content"] = [{"type": "text", "text": "refused"}, {"type": "text", "text": "Exit code: 2"}]
        [record] = toolerrors.collect(events, PROJECT, SCRATCH, HOME)
        self.assertEqual((record["exit_code"], record["output_tail"]), (2, "refused"))

    def test_only_covered_tools_that_exit_non_zero_are_kept(self) -> None:
        cases = [
            ("uv run --script run.py cad-record/scripts/validate.py .", 1, True),
            ("uv run --script run.py cad-model/scripts/cad_model.py run hw --label v2", 2, True),
            ("uv run --script run.py cad-render/scripts/render.py hw --version v2", 1, True),
            ("uv run --script run.py cad-record/scripts/validate.py .", 0, False),
            ("python3 -c 'print(1)'; exit 1", 1, False),
            ("cat validate.py.bak", 1, False),
        ]
        for command, code, kept in cases:
            with self.subTest(command=command, code=code):
                export = with_command(recorded_export(), command, "x", code)
                self.assertEqual(len(toolerrors.collect([export], PROJECT, SCRATCH, HOME)), int(kept))

    def test_the_tail_is_the_last_four_kilobytes(self) -> None:
        export = with_command(recorded_export(), STAMP, "a" * 5000 + "END", 2)
        tail = toolerrors.collect([export], PROJECT, SCRATCH, HOME)[0]["output_tail"]
        self.assertEqual((len(tail.encode("utf-8")), tail[-3:]), (4096, "END"))

    def test_a_truncated_output_is_read_from_the_saved_file_inside_the_run_home(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            home, project = scratch / "home", scratch / "project"
            saved = home / ".local/share/opencode/tool-output/tool_1"
            saved.parent.mkdir(parents=True)
            saved.write_text("head\n" + "line\n" * 10 + "the real error at the end\n", encoding="utf-8")
            preview = f"head\n[showing lines 1-1 of 12; full output saved to {saved}]"
            export = with_command(recorded_export(), STAMP.replace(str(PROJECT), str(project)), preview, 2)
            [record] = toolerrors.collect([export], project, scratch, home)
            self.assertTrue(record["output_tail"].endswith("the real error at the end"))

    def test_a_secret_shaped_value_in_the_output_or_command_is_redacted_and_a_digest_is_not(self) -> None:
        digest = "82f3dd2bae8b309989623acd46c1a93c9797993d534569fde816c21972aaf3e4"
        output = f"aws AKIAIOSFODNN7EXAMPLE token=abcdEFGH12345678xyz Authorization: Bearer abcdefghijklmnop1234 {digest}"
        export = with_command(recorded_export(), f"API_KEY=sk-abcdefghijklmnopqrstuvwx {STAMP}", output, 2)
        [record] = toolerrors.collect([export], PROJECT, SCRATCH, HOME)
        for secret in ("AKIAIOSFODNN7EXAMPLE", "abcdEFGH12345678xyz", "abcdefghijklmnop1234", "sk-abcdefghijklmnopqrstuvwx"):
            self.assertNotIn(secret, json.dumps(record))
        self.assertIn(digest, record["output_tail"])
        self.assertIn("token=[redacted]", record["output_tail"])


if __name__ == "__main__":
    unittest.main()
