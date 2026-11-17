import json
import sys
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
import metrics  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
HAIKU = "amazon-bedrock/global.anthropic.claude-haiku-5-5"
PRICES = metrics.load_prices(json.loads((BENCH / "config.json").read_text(encoding="utf-8"))["prices_usd_per_million"])


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def assistant(*parts: dict, tokens: dict | None = None, model: str = "global.anthropic.claude-haiku-5-5",
              created: int = 0, streamed: int = 0) -> dict:
    return {"type": "assistant", "model": {"id": model, "providerID": "amazon-bedrock"},
            "tokens": tokens or {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": created, "streamed": streamed, "completed": streamed}, "content": list(parts)}


def shell(command: str, exit_code: int | None = None, text: str = "") -> dict:
    metadata = {} if exit_code is None else {"exit": exit_code}
    return {"type": "tool", "name": "shell", "state": {"status": "completed", "input": {"command": command},
                                                      "content": [{"type": "text", "text": text}], "metadata": metadata},
            "time": {"created": 0, "ran": 0, "completed": 0}}


def dispatch(prompt: str, created: int, session: str | None = None, agent: str = "cad-part-designer") -> dict:
    payload = {"agent": agent, "description": "unit", "prompt": prompt}
    if session:
        payload["sessionID"] = session
    return {"type": "tool", "name": "subagent", "state": {"status": "completed", "input": payload, "metadata": {}},
            "time": {"created": created, "ran": created, "completed": created}}


def session(ident: str, *messages: dict, parent: str | None = None) -> dict:
    info = {"id": ident, **({"parentID": parent} if parent else {})}
    return {"info": info, "messages": list(messages)}


class RecordedExportTests(unittest.TestCase):
    """Exports recorded from real `opencode run` probes on haiku-5-5."""

    def test_a_recorded_session_counts_its_tools_tokens_and_the_providers_price(self) -> None:
        export = load("export-shell-question.json")
        result = metrics.summarize([export], export["info"]["id"], PRICES)
        self.assertEqual(result["tool_calls_by_tool"], {"read": 1, "shell": 1})
        self.assertEqual(result["model_turns"], 2)
        # Two steps: 4 + 12507 written, then 2 + 12507 read + 294 written.
        self.assertEqual(result["input_tokens"], 4 + 12507 + 2 + 12507 + 294)
        self.assertEqual(result["cached_input_tokens"], 12507)
        self.assertEqual(result["output_tokens"], 151 + 52)
        # 0.1/0.5/0.01/0.125 USD per million: 0.001639275 + 0.00018802; OpenCode reported the same.
        self.assertAlmostEqual(result["spend_usd"], 0.001827295, places=6)
        self.assertAlmostEqual(result["spend_usd"], result["opencode_reported_cost_usd"], places=6)
        self.assertEqual(result["unpriced_models"], [])

    def test_a_shell_command_that_is_not_a_build_is_no_failed_build(self) -> None:
        export = load("export-shell-question.json")
        self.assertEqual(metrics.summarize([export], export["info"]["id"], PRICES)["failed_builds"], 0)

    def test_the_lead_stopped_text_is_its_last_reply(self) -> None:
        text = metrics.last_assistant_text(load("export-shell-question.json"))
        self.assertTrue(text.endswith("Which colour do you prefer?"))

    def test_a_recorded_subagent_tree_names_its_child_and_counts_both_sessions(self) -> None:
        lead, child = load("export-lead-subagent.json"), load("export-child-subagent.json")
        self.assertEqual(metrics.child_session_ids(lead), [child["info"]["id"]])
        result = metrics.summarize([lead, child], lead["info"]["id"], PRICES)
        self.assertEqual((result["lead_model_turns"], result["subagent_model_turns"], result["model_turns"]), (3, 1, 4))
        self.assertEqual(result["subagent_dispatches"], 1)
        self.assertEqual(result["subagent_redispatches"], 0)
        self.assertEqual(result["dispatches_by_agent"], {"general": 1})

    def test_summarize_refuses_exports_without_the_lead(self) -> None:
        with self.assertRaises(ValueError):
            metrics.summarize([load("export-child-subagent.json")], "ses_missing", PRICES)


class BuildFailureTests(unittest.TestCase):
    def test_only_a_build_command_exiting_1_or_2_is_a_failed_build(self) -> None:
        cases = [
            (shell("uv run --script run.py cad-model/scripts/cad_model.py run hw --label v2", 1), True),
            (shell("uv run --script run.py cad-flow/scripts/scratch.py build --hw hw --unit part-a", None, "x\nExit code: 2"), True),
            (shell("uv run --script run.py cad-model/scripts/cad_model.py run hw --label v3", 0), False),
            (shell("uv run --script run.py cad-model/scripts/cad_model.py check v2 --hw hw", 1), False),
            (shell("uv run --script run.py cad-model/scripts/cad_model.py run hw --label v4", 137), False),
        ]
        for part, failed in cases:
            with self.subTest(command=part["state"]["input"]["command"]):
                self.assertEqual(metrics.is_failed_build(part), failed)


class DispatchTests(unittest.TestCase):
    def test_a_unit_sent_again_under_its_retry_id_is_one_redispatch_and_a_followup_is_not_a_dispatch(self) -> None:
        lead = session("ses_lead", assistant(
            dispatch("Unit id: part-arm. Design LMP-0002.", 1),
            dispatch("Unit id: part-base. Design LMP-0001.", 2),
            dispatch("Status?", 3, session="ses_child1"),
            dispatch("Unit id: part-arm-r2. Design LMP-0002 again.", 4),
        ))
        result = metrics.dispatch_metrics([lead])
        self.assertEqual(result["subagent_dispatches"], 3)
        self.assertEqual(result["subagent_redispatches"], 1)
        self.assertEqual(result["subagent_followups"], 1)
        self.assertEqual(result["redispatched_units"], ["part-arm"])

    def test_a_brief_without_a_unit_id_is_identified_by_agent_and_description(self) -> None:
        self.assertEqual(metrics.unit_id({"agent": "cad-mech-reviewer", "description": "Light review ", "prompt": "Review v3."}),
                         "cad-mech-reviewer:light review")


class PriceTests(unittest.TestCase):
    def test_a_request_over_a_tier_context_pays_the_tier_price(self) -> None:
        # 150 000 uncached input tokens exceed haiku's 100 000 tier: 0.5 USD per million, not 0.1.
        tokens = metrics.Tokens(input=150_000)
        self.assertAlmostEqual(tokens.cost(PRICES[HAIKU]), 0.075, places=9)
        self.assertAlmostEqual(metrics.Tokens(input=50_000).cost(PRICES[HAIKU]), 0.005, places=9)

    def test_an_unknown_model_is_reported_unpriced_not_free(self) -> None:
        lead = session("ses_lead", assistant(model="mystery-1", tokens={"input": 10, "output": 10, "cache": {}}))
        result = metrics.summarize([lead], "ses_lead", PRICES)
        self.assertEqual(result["unpriced_models"], ["amazon-bedrock/mystery-1"])

    def test_a_tier_without_its_context_size_is_rejected(self) -> None:
        with self.assertRaises(metrics.PriceError):
            metrics.load_prices({"m": {"input": 1, "output": 1, "cache_read": 1, "tiers": [{"input": 2, "output": 2, "cache_read": 2}]}})


class TimeTests(unittest.TestCase):
    def test_overlapping_tool_intervals_count_once(self) -> None:
        self.assertEqual(metrics.union_ms([(0, 10), (5, 15), (20, 25)]), 20)

    def test_model_time_runs_to_the_end_of_streaming_and_excludes_subagent_calls(self) -> None:
        tool = {"type": "tool", "name": "shell", "state": {}, "time": {"ran": 2000, "completed": 5000}}
        message = assistant(tool, dispatch("Unit id: part-a.", 0), created=0, streamed=1500)
        totals = metrics.session_totals(session("ses_lead", message))
        self.assertEqual((totals.model_ms, totals.tool_ms), (1500.0, 3000.0))


if __name__ == "__main__":
    unittest.main()
