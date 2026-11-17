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


class CacheIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.real = root / "real"
        for name in ("cad-flow", "opencode", "cad-concept", "development", "fontconfig", "uv", "pip", "ms-playwright"):
            (self.real / ".cache" / name).mkdir(parents=True)
        self.paths = run.Paths("r", root, root / "project", root / "home", root / "raw", root / "results", root / "run")
        self.environ = {"XDG_CACHE_HOME": str(self.real / ".cache"), "XDG_CONFIG_HOME": str(root / "nc"),
                        "XDG_DATA_HOME": str(root / "nd"), "XDG_STATE_HOME": str(root / "ns"),
                        "UV_CACHE_DIR": str(self.real / ".cache/development/uv"), "HOME": str(self.real)}

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_the_private_cache_links_only_the_allowlisted_package_download_caches(self) -> None:
        with mock.patch.dict(run.os.environ, self.environ, clear=True):
            run.link_farm(self.paths.home)
            env = run.opencode_env(self.paths)
        cache = self.paths.home / ".cache"
        self.assertEqual(env["XDG_CACHE_HOME"], str(cache))
        self.assertEqual(env["UV_CACHE_DIR"], str(self.real / ".cache/development/uv"))  # a package cache, shared
        # fontconfig, development, cad-flow and opencode start empty: they hold derived or run state.
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ["cad-concept", "ms-playwright", "pip", "uv"])
        self.assertTrue(all(p.is_symlink() for p in cache.iterdir()))

    def test_the_private_cache_check_accepts_allowlisted_links_and_real_directories_only(self) -> None:
        with mock.patch.dict(run.os.environ, self.environ, clear=True):
            run.link_farm(self.paths.home)
            allowed = run.package_caches()
        cache, home = self.paths.home / ".cache", self.paths.home
        (cache / "opencode").mkdir()  # what OpenCode creates in the run: a real directory inside the home
        self.assertEqual(run.isolation.private_cache_findings(cache, allowed, home), [])
        (cache / "fontconfig").symlink_to(self.real / ".cache/fontconfig")
        (cache / "pip").unlink()
        (cache / "pip").symlink_to(self.real / ".cache/development")  # an allowlisted name, the wrong target
        (cache / "stray.txt").write_text("x", encoding="utf-8")
        self.assertEqual(sorted(run.isolation.private_cache_findings(cache, allowed, home)), [
            f"private cache entry fontconfig links outside the allowlist: {self.real / '.cache/fontconfig'}",
            f"private cache entry pip links outside the allowlist: {self.real / '.cache/development'}",
            "private cache entry stray.txt is not a directory",
        ])

    def test_a_package_cache_variable_outside_the_home_must_name_an_allowlisted_cache(self) -> None:
        home = self.paths.home
        good = {"UV_CACHE_DIR": "/shared/development/uv", "PIP_CACHE_DIR": "/shared/development/pip"}
        self.assertEqual(run.isolation.package_cache_env_findings(good, home), [])
        self.assertEqual(run.isolation.package_cache_env_findings({"UV_CACHE_DIR": "/shared/fontconfig"}, home),
                         ["UV_CACHE_DIR points outside the run home at a cache that is not allowlisted: /shared/fontconfig"])

    def test_without_a_uv_cache_dir_the_real_uv_cache_is_named_explicitly(self) -> None:
        environ = {key: value for key, value in self.environ.items() if key != "UV_CACHE_DIR"}
        with mock.patch.dict(run.os.environ, environ, clear=True):
            env = run.opencode_env(self.paths)
        self.assertEqual(env["UV_CACHE_DIR"], str(self.real / ".cache/uv"))

    def test_the_cache_check_fails_unless_the_effective_cache_dirs_are_inside_the_run_home(self) -> None:
        home = self.paths.home
        good = {"XDG_CACHE_HOME": str(home / ".cache")}
        self.assertEqual(run.isolation.cache_findings(str(home / ".cache/opencode"), good, home), [])
        self.assertEqual(run.isolation.cache_findings(str(self.real / ".cache/opencode"), good, home),
                         [f"opencode cache outside the run home: {self.real / '.cache/opencode'}"])
        self.assertEqual(run.isolation.cache_findings(str(home / ".cache/opencode"), {}, home),
                         ["XDG_CACHE_HOME is not set inside the run home: None"])
        self.assertEqual(run.isolation.cache_findings(None, good, home), ["opencode reported no cache path"])

    def test_a_running_warm_up_the_run_recorded_is_stopped_by_its_recorded_session_only(self) -> None:
        warm = self.paths.home / ".cache/cad-flow/warm"
        warm.mkdir(parents=True)
        child = run.subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "preflight.py", "--warm"],
                                     start_new_session=True)
        stranger = run.subprocess.Popen(["sleep", "62"], start_new_session=True)
        try:
            (warm / "k.json").write_text(json.dumps({"state": "running", "pid": child.pid}), encoding="utf-8")
            (warm / "j.json").write_text(json.dumps({"state": "running", "pid": stranger.pid}), encoding="utf-8")
            results = {Path(r["record"]).name: r for r in run.stop_recorded_warmups(self.paths, grace_s=0.3)}
            self.assertEqual(results["k.json"]["stopped"], True)
            self.assertIsNotNone(child.wait(timeout=10))
            self.assertEqual(results["j.json"]["stopped"], False)  # its pid is no preflight --warm: left alone
            self.assertIsNone(stranger.poll())
        finally:
            stranger.kill()  # exact pid this test started
            stranger.wait()
            if child.poll() is None:
                child.kill()
                child.wait()


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
    def test_the_gate_context_reads_flow_stage_and_whether_an_l1_version_is_built(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            hw = Path(folder)
            self.assertEqual(run.gate_context(hw, run.built_versions(hw)), run.dialogue.GateContext(None, False))
            (hw / "product.yaml").write_text("flow: {stage: concept}\n", encoding="utf-8")
            write_version(hw, "v1", "layout")
            (hw / "product.yaml").write_text("flow: {stage: parts}\n", encoding="utf-8")
            self.assertEqual(run.gate_context(hw, run.built_versions(hw)), run.dialogue.GateContext("parts", True, False))
            write_version(hw, "v2", "parts")
            self.assertEqual(run.gate_context(hw, run.built_versions(hw)), run.dialogue.GateContext("parts", True, True))

    def test_a_layout_freeze_prompt_before_any_l1_version_is_not_a_gate_in_the_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project/hw").mkdir(parents=True)
            (root / "project/hw/product.yaml").write_text("flow: {stage: ground}\n", encoding="utf-8")
            paths = run.Paths("r", root, root / "project", root / "home", root / "raw", root / "results", root / "run")
            script = run.load_answers(run.CASES / "wall-bracket")
            driver = FakeDriver(paths, [(0, None, "Do you approve the LAYOUT freeze on v2? Which colour should it be?")])
            conversation = run.converse(driver, script, "fixture", "start", {**CAPS, "max_turns": 1})
            self.assertEqual(conversation.outcome, "cap:turns")  # not fail:unmatched-at-gate: no L1 version exists
            self.assertEqual((conversation.entries[0]["stage"], conversation.entries[0]["has_l1"]), ("ground", False))

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
