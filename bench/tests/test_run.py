import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
import dialogue  # noqa: E402
import run  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"

SCRIPT = dialogue.load_script({
    "default": "Use your recommendation.", "nudge": "Continue.", "wait_s": {"question": 60, "gate": 300},
    "stop": {"bench": "parts-gate", "fixture": "layout"},
    "intents": [
        {"id": "parts-gate", "kind": "gate", "match": ["PARTS gate"], "answer": "PARTS approved."},
        {"id": "layout", "kind": "gate", "match": ["LAYOUT freeze"], "answer": "Frozen."},
        {"id": "loads", "match": ["load"], "answer": "200 N."},
        {"id": "profile", "profile": True, "match": ["flow profile"], "answer": "Default."},
    ],
})
CAPS = {"max_turns": 10, "max_active_minutes": 60, "run_spend_usd": 40}


def write_version(hw: Path, label: str, stage: str) -> None:
    folder = hw / "versions" / label
    (folder / "record").mkdir(parents=True)
    (folder / "manifest.json").write_text("{}", encoding="utf-8")
    (folder / "record/product.yaml").write_text(f"flow: {{stage: {stage}}}\n", encoding="utf-8")


class FakeDriver(run.Driver):
    """Plays back the lead's replies: (exit code, cap hit, text); writes an L2 version when told to."""

    def __init__(self, paths: run.Paths, turns: list[tuple[int, str | None, str]], build_at: int | None = None) -> None:
        super().__init__(paths, {}, None, None, 40.0)
        self.turns, self.build_at, self.sent = turns, build_at, []

    def send(self, message: str, deadline: float) -> run.Invocation:
        self.sent.append(message)
        code, killed, _text = self.turns[len(self.invocations)]
        if self.build_at == len(self.invocations) + 1:
            write_version(self.paths.project / "hw", "v3", "parts")
        self.session = "ses_lead"
        invocation = run.Invocation(len(self.invocations) + 1, 2.0, code, killed, self.session)
        self.invocations.append(invocation)
        return invocation

    def export(self, session: str, sanitize: bool = False) -> dict:
        text = self.turns[len(self.invocations) - 1][2]
        return {"info": {"id": session}, "messages": [{"type": "user"}, {"type": "assistant", "content": [{"type": "text", "text": text}]}]}


class ConverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        (root / "project/hw").mkdir(parents=True)
        self.paths = run.Paths("r1", root, root / "project", root / "home", root / "raw", root / "results", root / "results/runs/r1")

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_the_run_ends_one_turn_after_the_stop_gate_is_answered_on_a_built_version(self) -> None:
        driver = FakeDriver(self.paths, [
            (0, None, "What loads apply? Which flow profile should I use?"),
            (0, None, "v3 passed the full check. Do you approve the PARTS gate?"),
            (0, None, "Recorded DR-009. Stopping."),
        ], build_at=2)
        conversation = run.converse(driver, SCRIPT, "bench", "start", CAPS)
        self.assertEqual(conversation.outcome, "reached")
        self.assertEqual(driver.sent[1], "Answers from the user:\n- 200 N.\n- Default.")
        self.assertTrue(driver.sent[2].endswith(dialogue.STOP_NOTE))
        metrics = run.dialogue_metrics(conversation, driver)
        self.assertEqual((metrics["harness_turns"], metrics["questions_asked"], metrics["human_wait_events"],
                          metrics["human_wait_s"], metrics["gates_answered"], metrics["profile_asked"]),
                         (3, 3, 2, 360.0, 1, True))

    def test_a_stop_gate_named_before_its_version_exists_does_not_end_the_run(self) -> None:
        driver = FakeDriver(self.paths, [(0, None, "Do you approve the PARTS gate?")] * 3)
        conversation = run.converse(driver, SCRIPT, "bench", "start", {**CAPS, "max_turns": 3})
        self.assertEqual(conversation.outcome, "cap:turns")
        self.assertEqual(len(driver.invocations), 3)

    def test_a_cap_the_driver_hit_ends_the_run_as_that_cap(self) -> None:
        driver = FakeDriver(self.paths, [(0, None, "What loads apply?"), (-15, "spend", "")])
        self.assertEqual(run.converse(driver, SCRIPT, "bench", "start", CAPS).outcome, "cap:spend")

    def test_two_failed_invocations_in_a_row_end_the_run_as_an_error(self) -> None:
        driver = FakeDriver(self.paths, [(0, None, "What loads apply?"), (1, None, ""), (1, None, "")])
        self.assertEqual(run.converse(driver, SCRIPT, "bench", "start", CAPS).outcome, "error")

    def test_a_provider_error_in_the_recorded_export_ends_the_run_as_a_provider_outage_at_once(self) -> None:
        outage = json.loads((DATA / "export-provider-auth-outage.json").read_text(encoding="utf-8"))

        class OutageDriver(FakeDriver):
            def export(self, session: str, sanitize: bool = False) -> dict:
                return outage if self.invocations[-1].exit_code else super().export(session, sanitize)

        driver = OutageDriver(self.paths, [(0, None, "What loads apply?"), (1, None, ""), (0, None, "")])
        conversation = run.converse(driver, SCRIPT, "bench", "start", CAPS)
        self.assertEqual(conversation.outcome, "aborted:provider-outage")
        self.assertEqual(len(driver.invocations), 2)
        self.assertIn("credential", conversation.entries[-1]["provider_error"])

    def test_a_stop_gate_the_lead_approved_without_being_answered_fails_the_run(self) -> None:
        class SelfApprovingDriver(FakeDriver):
            def send(self, message: str, deadline: float) -> run.Invocation:
                invocation = super().send(message, deadline)
                decisions = self.paths.project / "hw/decisions"
                decisions.mkdir(exist_ok=True)
                (decisions / "DR-009.md").write_text("Status: APPROVED\n# DR-009 PARTS gate approved on v3\n", encoding="utf-8")
                return invocation

        driver = SelfApprovingDriver(self.paths, [(0, None, "The session stops here, as you scoped it."), (0, None, "")],
                                     build_at=1)
        conversation = run.converse(driver, SCRIPT, "bench", "start", CAPS)
        self.assertEqual(conversation.outcome, "fail:gate-approved-unasked")
        self.assertEqual(len(driver.sent), 1)

    def test_an_unmatched_question_at_a_gate_fails_the_run_without_answering_it(self) -> None:
        driver = FakeDriver(self.paths, [
            (0, None, "What loads apply? What colour should it be?"),
            (0, None, "v3 passed the full check. Do you approve the PARTS gate? Should I add a chamfer?"),
            (0, None, "never reached"),
        ], build_at=2)
        conversation = run.converse(driver, SCRIPT, "bench", "start", CAPS)
        self.assertEqual(conversation.outcome, "fail:unmatched-at-gate")
        self.assertEqual(len(driver.sent), 2)  # the gate reply was never sent
        self.assertEqual(run.unmatched_questions(conversation), [
            {"turn": 1, "text": "What colour should it be?", "at_gate": False},
            {"turn": 2, "text": "Should I add a chamfer?", "at_gate": True},
        ])
        metrics = run.dialogue_metrics(conversation, driver)
        self.assertEqual((metrics["questions_unmatched"], metrics["questions_unmatched_at_gate"], metrics["gates_answered"],
                          metrics["human_wait_events"]), (2, 1, 0, 1))

    def test_the_recorded_ground_reply_counts_and_lists_its_unmatched_question(self) -> None:
        text = json.loads((DATA / "events-ground-print-in-place.jsonl").read_text(encoding="utf-8"))["part"]["text"]
        driver = FakeDriver(self.paths, [(0, None, text)])
        script = run.load_answers(run.CASES / "print-in-place")
        conversation = run.converse(driver, script, "fixture", "start", {**CAPS, "max_turns": 1})
        self.assertEqual(conversation.outcome, "cap:turns")
        self.assertEqual(run.unmatched_questions(conversation), [{
            "turn": 1, "at_gate": False,
            "text": "Lid retention: does the lid just rest on the rim by gravity, or must it snap shut?"}])
        self.assertEqual(run.dialogue_metrics(conversation, driver)["questions_unmatched"], 1)


class ToolErrorFileTests(unittest.TestCase):
    def test_tool_errors_jsonl_holds_one_line_per_failed_repo_tool_call_and_exists_when_empty(self) -> None:
        export = json.loads((DATA / "export-shell-question.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = run.Paths("r", root, root / "project", root / "home", root / "raw", root / "results", root / "run")
            paths.run_dir.mkdir()
            self.assertEqual(run.write_tool_errors(paths, [export]), 0)  # the recorded `echo hi; exit 1` is no repo tool
            self.assertEqual((paths.run_dir / "tool-errors.jsonl").read_text(encoding="utf-8"), "")
            shell = next(p for m in export["messages"] if m["type"] == "assistant" for p in m["content"] if p.get("name") == "shell")
            shell["state"]["input"]["command"] = f"cd {paths.project} && uv run --script run.py cad-recheck/scripts/stamp.py --verify"
            shell["state"]["metadata"]["exit"] = 2
            self.assertEqual(run.write_tool_errors(paths, [export]), 1)
            [line] = (paths.run_dir / "tool-errors.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(line)["argv"][:2], ["cd", "."])


class BudgetTests(unittest.TestCase):
    def test_a_run_refuses_to_start_once_the_ledger_reaches_the_configured_total_cap(self) -> None:
        config = json.loads(run.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["caps"]["total_spend_usd"], 500)
        with tempfile.TemporaryDirectory() as folder:
            results = Path(folder)
            (results / "spend.jsonl").write_text(json.dumps({"total_usd": 500.0}) + "\n", encoding="utf-8")
            args = run.argparse.Namespace(case="wall-bracket", results=results, budget=None, run_id="b")
            with mock.patch.object(run, "check_results_checkout"), mock.patch.object(run, "Paths") as paths:
                with self.assertRaisesRegex(run.BenchError, "budget"):
                    run.execute(args, config)
                paths.create.assert_not_called()


class LeftoverTests(unittest.TestCase):
    def test_the_users_cad_viewer_state_is_not_linked_into_the_throwaway_home(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real_state = root / "real/state"
            for name in ("cad-viewer", "opencode", "mise"):
                (real_state / name).mkdir(parents=True)
            with mock.patch.dict(run.os.environ, {"XDG_STATE_HOME": str(real_state), "XDG_CONFIG_HOME": str(root / "nc"),
                                                  "XDG_DATA_HOME": str(root / "nd")}):
                run.link_farm(root / "home")
            state = root / "home/.local/state"
            self.assertEqual(sorted(p.name for p in state.iterdir()), ["mise"])
            self.assertTrue((state / "mise").is_symlink())

    def test_the_viewer_sessions_to_stop_are_the_ones_in_the_throwaway_homes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            session = home / ".local/state/cad-viewer/abc/session.json"
            session.parent.mkdir(parents=True)
            session.write_text('{"pid": 1}', encoding="utf-8")
            self.assertEqual(run.recorded_viewer_sessions(home), [session])

    def test_only_the_runs_own_process_groups_are_stopped_and_a_scratch_process_outside_them_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder).resolve()
            ours = run.subprocess.Popen(["sleep", "60"], cwd=scratch, start_new_session=True)
            run.own_group(ours)
            stranger = run.subprocess.Popen(["sleep", "61"], cwd=scratch, start_new_session=True)  # not registered
            try:
                report = run.kill_leftovers(scratch, grace_s=0.3)
                self.assertIsNotNone(ours.poll())
                self.assertIsNone(stranger.poll())
                self.assertEqual(report["own_groups_stopped"], [ours.pid])
                self.assertIn(stranger.pid, [entry["pid"] for entry in report["still_running"]])
            finally:
                stranger.kill()  # exact pid of the process this test started
                stranger.wait()
                ours.wait()


class MarkerTests(unittest.TestCase):
    def test_a_run_claims_its_own_marker_directory_and_refuses_a_used_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(run, "MARKER_ROOT", Path(folder)):
            (Path(folder) / "r1").mkdir()  # a launcher may create it first for the log
            markers = run.claim_markers("r1")
            self.assertEqual((markers / "pid").read_text(encoding="utf-8").strip(), str(run.os.getpid()))
            with self.assertRaises(run.BenchError):
                run.claim_markers("r1")
            (Path(folder) / "r2").mkdir()
            (Path(folder) / "r2/done").write_text("0\n", encoding="utf-8")  # a stale marker from an old run
            with self.assertRaises(run.BenchError):
                run.claim_markers("r2")


class IsolationAbortTests(unittest.TestCase):
    def test_a_failed_isolation_check_records_isolation_failed_and_never_drives_the_lead(self) -> None:
        failed = {"passed": False, "findings": ["skill loaded from outside the throwaway home: /Users/x/.agents/skills/a"],
                  "loaded": {}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "results").mkdir()
            args = run.argparse.Namespace(case="wall-bracket", arm="a", mode="bench", results=root / "results", phase="t",
                                          model=None, agent=None, seed_hw=None, max_turns=None, max_active_minutes=None,
                                          spend_cap=None, budget=None, run_id="iso", no_judge=True, no_push=True,
                                          keep_scratch=False)
            config = json.loads(run.CONFIG.read_text(encoding="utf-8"))
            published: list[dict] = []
            with mock.patch.multiple(run, TMP=root, check_results_checkout=mock.DEFAULT, prepare_project=mock.DEFAULT,
                                     install_harness=mock.DEFAULT, kill_leftovers=mock.DEFAULT, repo_commit=mock.Mock(return_value="abc123"),
                                     check_isolation=mock.Mock(return_value=failed), run_session=mock.DEFAULT,
                                     publish=mock.Mock(side_effect=lambda paths, record, phase, push: published.append(record) or 0.0)) as patched, \
                    contextlib.redirect_stdout(io.StringIO()):
                code = run.execute(args, config)
            self.assertEqual(code, 4)
            patched["run_session"].assert_not_called()
            record = json.loads((root / "results/runs/iso/record.json").read_text(encoding="utf-8"))
            self.assertEqual((record["outcome"], record["accepted"], record["spend"]["total_usd"]), ("isolation_failed", False, 0.0))
            self.assertEqual(record["isolation"]["findings"], failed["findings"])
            self.assertEqual(published[0]["outcome"], "isolation_failed")
            self.assertFalse((root / "bench-iso").exists())  # scratch removed


class RecordStateTests(unittest.TestCase):
    def test_the_layout_counts_frozen_once_the_stage_moved_on_with_an_approved_layout_decision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            hw = Path(folder)
            (hw / "decisions").mkdir()
            (hw / "product.yaml").write_text("flow: {stage: parts}\n", encoding="utf-8")
            (hw / "decisions/DR-005.md").write_text("Status: PROVISIONAL\n# LAYOUT freeze v2\n", encoding="utf-8")
            self.assertFalse(run.layout_frozen(hw).passed)
            (hw / "decisions/DR-006.md").write_text("Status: APPROVED\n# LAYOUT freeze v2\n", encoding="utf-8")
            self.assertTrue(run.layout_frozen(hw).passed)

    def test_built_versions_read_the_stage_each_snapshot_was_built_at(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            hw = Path(folder)
            write_version(hw, "v1", "layout")
            write_version(hw, "v2", "parts")
            (hw / "versions/v3").mkdir()  # an unsealed, partial version is not a built version
            self.assertEqual(run.built_versions(hw), {"v1": "layout", "v2": "parts"})

    def test_the_opening_message_is_the_installed_cad_flow_command_with_the_brief_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            commands = root / "home/.config/opencode/commands"
            commands.mkdir(parents=True)
            (commands / "cad-flow.md").write_text((run.ROOT / "harness/opencode/commands/cad-flow.md").read_text(encoding="utf-8"),
                                                  encoding="utf-8")
            paths = run.Paths("r", root, root, root / "home", root, root, root)
            message = run.first_message(paths, run.CASES / "wall-bracket", "bench")
            self.assertTrue(message.startswith("Read the `cad-flow` skill and follow its workflow."))
            self.assertIn("200 N", message)
            self.assertIn(run.SCOPE["bench"], message)
            self.assertNotIn("$ARGUMENTS", message)


if __name__ == "__main__":
    unittest.main()
