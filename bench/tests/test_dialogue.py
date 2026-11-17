import json
import sys
import unittest
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
import dialogue  # noqa: E402
import metrics  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
CASES = BENCH / "cases"


def case_script(case: str) -> dialogue.Script:
    data = yaml.safe_load((CASES / case / "answers.yaml").read_text(encoding="utf-8"))
    common = yaml.safe_load((CASES / "_common" / "answers.yaml").read_text(encoding="utf-8"))
    return dialogue.load_script(dialogue.merge(data, common))


SCRIPT = dialogue.load_script({
    "default": "Use your recommendation.",
    "nudge": "Continue.",
    "wait_s": {"question": 60, "gate": 300, "nudge": 5},
    "stop": {"bench": "parts-gate"},
    "intents": [
        {"id": "parts-gate", "kind": "gate", "match": ["PARTS gate"], "answer": "PARTS approved."},
        {"id": "colour", "match": ["colou?r"], "answer": "Blue."},
        {"id": "profile", "profile": True, "match": ["flow profile"], "answer": "Default profile."},
    ],
})


class AskTests(unittest.TestCase):
    def test_question_sentences_and_reply_requests_are_asks_and_statements_are_not(self) -> None:
        text = ("I built v3 and the checks passed. Which colour do you prefer?\n"
                "1. **Mounting:** how many screws hold it?\n"
                "Reply `approve` to close the PARTS gate.\n"
                "I recorded the approval in DR-004.")
        self.assertEqual(dialogue.extract_asks(text), [
            "Which colour do you prefer?",
            "Mounting: how many screws hold it?",
            "Reply approve to close the PARTS gate.",
        ])

    def test_a_reply_without_any_ask_is_a_nudge(self) -> None:
        reply = dialogue.respond(SCRIPT, "The sweep is running in the background.", "bench")
        self.assertEqual((reply.kind, reply.message, reply.asks, reply.stops), ("nudge", "Continue.", (), False))


class RespondTests(unittest.TestCase):
    def test_a_matched_question_gets_its_answer_and_an_unmatched_one_the_default(self) -> None:
        reply = dialogue.respond(SCRIPT, "Which colour do you prefer? What finish do you need?", "bench")
        self.assertEqual([(ask.intent, ask.matched) for ask in reply.asks], [("colour", True), (None, False)])
        self.assertEqual(reply.message, "Answers from the user:\n- Blue.\n- Use your recommendation.")
        self.assertEqual((reply.kind, reply.wait_s), ("question", 60))

    def test_the_recorded_lead_question_matches_its_intent(self) -> None:
        text = metrics.last_assistant_text(json.loads((DATA / "export-shell-question.json").read_text(encoding="utf-8")))
        reply = dialogue.respond(SCRIPT, text, "bench")
        self.assertEqual([ask.intent for ask in reply.asks], ["colour"])

    def test_the_modes_stop_gate_ends_the_run_only_when_its_version_exists(self) -> None:
        text = "v4 passed the full check. Do you approve the PARTS gate?"
        stopping = dialogue.respond(SCRIPT, text, "bench", can_stop=True)
        self.assertTrue(stopping.stops)
        self.assertTrue(stopping.message.endswith(dialogue.STOP_NOTE))
        self.assertEqual((stopping.kind, stopping.wait_s), ("gate", 300))
        early = dialogue.respond(SCRIPT, text, "bench", can_stop=False)
        self.assertFalse(early.stops)
        self.assertNotIn(dialogue.STOP_NOTE, early.message)
        self.assertFalse(dialogue.respond(SCRIPT, text, "fixture").stops)

    def test_a_flow_profile_question_is_flagged(self) -> None:
        reply = dialogue.respond(SCRIPT, "Which flow profile should this project use?", "bench")
        self.assertEqual([(ask.intent, ask.profile) for ask in reply.asks], [("profile", True)])


class ScriptTests(unittest.TestCase):
    def test_a_stop_naming_no_intent_is_rejected(self) -> None:
        with self.assertRaises(dialogue.ScriptError):
            dialogue.load_script({"default": "d", "nudge": "n", "stop": {"bench": "missing"},
                                  "intents": [{"id": "a", "match": ["a"], "answer": "a"}]})

    def test_merge_puts_shared_gates_first_then_case_intents_and_fills_slots(self) -> None:
        common = {"default": "d", "nudge": "n", "intents": [
            {"id": "gate", "kind": "gate", "match": ["gate"], "answer": "ok"},
            {"id": "loads", "match": ["load"]},
            {"id": "unused-slot", "match": ["x"]},
            {"id": "setup", "match": ["setup"], "answer": "yes"},
        ]}
        case = {"slots": {"loads": "200 N"}, "intents": [{"id": "rail", "match": ["rail"], "answer": "40 mm"}]}
        merged = dialogue.merge(case, common)
        self.assertEqual([(i["id"], i["answer"]) for i in merged["intents"]],
                         [("gate", "ok"), ("rail", "40 mm"), ("loads", "200 N"), ("setup", "yes")])

    def test_a_slot_the_shared_file_does_not_define_is_rejected(self) -> None:
        with self.assertRaises(dialogue.ScriptError):
            dialogue.merge({"slots": {"nope": "x"}}, {"intents": []})


def recorded_text(name: str) -> str:
    """The lead's final text event of a recorded `opencode run --format json` turn."""
    events = [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [event["part"]["text"] for event in events if event["type"] == "text"][-1]


class RecordedGroundTests(unittest.TestCase):
    """Real GROUND replies the lead gave in the fixture builds: long question tables, not gates."""

    def test_regression_a_ground_reply_that_describes_later_gates_approves_no_gate(self) -> None:
        # The first matcher read "Once you answer, I'll ... ask for your approval. Then come the concept
        # variants, the VARIANT gate and the L1 LAYOUT with its freeze gate." as an ask and answered it with
        # the VARIANT, INTENT and LAYOUT freeze approvals before the lead had presented any of them.
        for case in ("wall-bracket", "fdm-enclosure", "print-in-place"):
            with self.subTest(case=case):
                script = case_script(case)
                gates = {intent.id: intent.answer for intent in script.intents if intent.kind == "gate"}
                reply = dialogue.respond(script, recorded_text(f"events-ground-{case}.jsonl"), "fixture", can_stop=False)
                self.assertEqual([ident for ident in reply.answered if ident in gates], [])
                self.assertEqual(reply.kind, "question")
                for answer in gates.values():
                    self.assertNotIn(answer, reply.message)

    def test_a_ground_reply_that_names_later_gates_is_not_a_gate_and_its_unmatched_question_gets_the_default(self) -> None:
        script = case_script("fdm-enclosure")
        reply = dialogue.respond(script, recorded_text("events-ground-fdm-enclosure.jsonl"), "fixture", can_stop=False)
        self.assertFalse(reply.at_gate)
        self.assertFalse(reply.unmatched_at_gate)
        self.assertEqual([ask.text for ask in reply.unmatched], ["Q-017 | Do you need vents, LED windows, buttons or rubber feet?"])
        self.assertEqual(reply.message.splitlines()[-1], f"- {script.default}")
        self.assertNotIn("approved", reply.message.lower())

    def test_a_follow_up_sentence_is_matched_through_its_line(self) -> None:
        reply = dialogue.respond(case_script("wall-bracket"), recorded_text("events-ground-wall-bracket.jsonl"), "fixture")
        follow_up = [ask for ask in reply.asks if ask.text == "Or kept out of git?"]
        self.assertEqual([ask.intent for ask in follow_up], ["storage"])

    def test_regression_an_abbreviation_does_not_split_the_release_scope_question(self) -> None:
        # The first matcher split Q-011 at "e.g." and answered the orphan "at 300 N?" with the default, so the
        # lead adopted its own proof-test suggestion instead of receiving the CAD-only release scope.
        reply = dialogue.respond(case_script("wall-bracket"), recorded_text("events-ground-wall-bracket.jsonl"), "fixture")
        q011 = [ask for ask in reply.asks if ask.text.startswith("Q-011")]
        self.assertEqual([ask.intent for ask in q011], ["release-scope"])
        self.assertTrue(q011[0].text.endswith("e.g. at 300 N?"))
        self.assertNotIn("at 300 N?", [ask.text for ask in reply.asks])


class AbbreviationTests(unittest.TestCase):
    def test_a_question_with_an_abbreviation_inside_is_one_ask(self) -> None:
        for abbreviation in ("e.g.", "i.e.", "etc.", "vs.", "cf."):
            with self.subTest(abbreviation=abbreviation):
                question = f"Will you also load-test a first bracket, {abbreviation} at 300 N?"
                self.assertEqual(dialogue.extract_asks(f"Q-011 | {question}"), [f"Q-011 | {question}"])

    def test_a_sentence_ending_in_a_period_still_splits_before_the_next_question(self) -> None:
        self.assertEqual(dialogue.extract_asks("The rail is bare. Which finish do you want?"), ["Which finish do you want?"])

    def test_approx_fig_and_no_do_not_split_a_question(self) -> None:
        for question in ("Is the wall approx. 2 mm or more?", "Is the wall APPROX. 2 mm or more?",
                         "Does the bore in Fig. 3 need a chamfer?", "Is part No. 4 the lid?"):
            with self.subTest(question=question):
                self.assertEqual(dialogue.extract_asks(question), [question])

    def test_a_lowercase_no_at_the_end_of_a_sentence_still_ends_it(self) -> None:
        self.assertEqual(dialogue.extract_asks("The answer is no. Which colour?"), ["Which colour?"])

    def test_regression_a_bracketed_aside_ending_in_a_full_stop_ends_its_sentence(self) -> None:
        # Recorded print-in-place GROUND reply: "Size tolerance: ...? [±1 mm.] And what general tolerance should
        # drawings state?" glued the bracketed suggestion onto the next question.
        reply = dialogue.respond(case_script("print-in-place"), recorded_text("events-ground-print-in-place.jsonl"), "fixture")
        tolerance = [ask for ask in reply.asks if "general tolerance" in ask.text]
        self.assertEqual([(ask.text, ask.intent) for ask in tolerance],
                         [("And what general tolerance should drawings state?", "accuracy")])
        self.assertEqual(dialogue.extract_asks("The gap is 0.3 mm (ISO 2768-m.) Which finish?"), ["Which finish?"])


class OfferTests(unittest.TestCase):
    def test_regression_the_recorded_commit_offer_without_a_question_mark_is_a_commit_timing_ask(self) -> None:
        # Recorded fdm-enclosure turn 2, "## Not committed to git": "I can commit once L2 passes, or commit the
        # layout state now if you prefer." The lead offered a choice; the matcher nudged instead.
        reply = dialogue.respond(case_script("fdm-enclosure"), recorded_text("events-gate-fdm-enclosure-layout.jsonl"), "fixture")
        self.assertEqual([(ask.text, ask.intent) for ask in reply.asks],
                         [("I can commit once L2 passes, or commit the layout state now if you prefer.", "commit-timing")])

    def test_an_offer_cue_in_a_statement_line_takes_only_its_own_sentence(self) -> None:
        self.assertEqual(dialogue.extract_asks("The sweep passed. I can run a finer grid if you'd rather."),
                         ["I can run a finer grid if you'd rather."])


class HeadingTests(unittest.TestCase):
    def test_regression_a_markdown_heading_is_never_a_question(self) -> None:
        # Recorded fdm-enclosure turn 2: "## Please confirm before L2" was taken for an unmatched question.
        text = recorded_text("events-gate-fdm-enclosure-layout.jsonl")
        self.assertNotIn("Please confirm before L2", dialogue.extract_asks(text))
        self.assertEqual(dialogue.extract_asks("## Which variant do you prefer?\nThe scene is ready."), [])

    def test_the_recorded_heading_at_a_gate_does_not_fail_the_gate(self) -> None:
        text = recorded_text("events-gate-fdm-enclosure-layout.jsonl") + "\nDo you approve the LAYOUT freeze on v4?"
        reply = dialogue.respond(case_script("fdm-enclosure"), text, "fixture", can_stop=True)
        self.assertTrue(reply.at_gate)
        self.assertFalse(reply.unmatched_at_gate)
        self.assertEqual([ask.intent for ask in reply.asks], ["commit-timing", "layout-freeze"])

    def test_a_heading_still_names_the_gate_for_the_approval_request_below_it(self) -> None:
        reply = dialogue.respond(case_script("wall-bracket"), "## Decision needed: LAYOUT freeze on v2\nApprove or name changes.",
                                 "fixture", can_stop=True)
        self.assertEqual([ask.intent for ask in reply.asks], ["layout-freeze"])


class DecisionBlockTests(unittest.TestCase):
    def test_regression_the_recorded_decision_block_is_one_question_with_its_options(self) -> None:
        # Recorded wall-bracket turn 3 ends with "**Decision for you:**" and two option bullets, no "?";
        # it went unanswered.
        reply = dialogue.respond(case_script("wall-bracket"), recorded_text("events-decision-wall-bracket-commit.jsonl"),
                                 "fixture", can_stop=True)
        self.assertEqual(len(reply.asks), 1)
        ask = reply.asks[0]
        self.assertEqual((ask.intent, ask.matched), ("commit-timing", True))
        self.assertEqual(len(ask.options), 2)
        self.assertTrue(ask.options[0].startswith("Commit now with flow.stage: layout."))
        self.assertTrue(ask.options[1].startswith("Commit at the end of L2."))
        self.assertEqual(reply.kind, "question")

    def test_a_decision_block_no_intent_matches_is_one_unmatched_question_answered_by_default(self) -> None:
        for label in ("Decision for you:", "**Decision for you:**", "Please choose:", "**Please choose:**"):
            with self.subTest(label=label):
                text = f"The hinge can take two shapes.\n{label}\n- A barrel hinge.\n- A living hinge.\nThen I continue."
                reply = dialogue.respond(SCRIPT, text, "bench")
                self.assertEqual([(a.matched, a.options) for a in reply.asks], [(False, ("A barrel hinge.", "A living hinge."))])
                self.assertEqual(reply.message, f"Answers from the user:\n- {SCRIPT.default}")

    def test_a_question_inside_an_option_bullet_is_not_asked_twice(self) -> None:
        text = "Please choose:\n- Keep the colour?\n- Change it?"
        self.assertEqual(len(dialogue.respond(SCRIPT, text, "bench").asks), 1)


class RoutingTests(unittest.TestCase):
    def test_the_recorded_small_mechanism_ground_questions_reach_their_answers(self) -> None:
        # The second small-mechanism build answered 12 of its 30 GROUND questions with the default.
        reply = dialogue.respond(case_script("small-mechanism"), recorded_text("events-ground-small-mechanism.jsonl"), "fixture")
        routes = {ask.text: ask.intent for ask in reply.asks}
        self.assertEqual(routes["What kind of use is this?"], "service-class")
        self.assertEqual(routes["Does anything pull on the lever when it's latched?"], "latched-load")
        self.assertEqual(routes["Should it stay put at 0° or 75° (a detent or snap), or are the two hard stops all it needs?"], "detent")
        self.assertEqual(routes["Do you have a Ø6 H7 reamer (or the size you plan to ream to)?"], "equipment")
        self.assertEqual([ask.text for ask in reply.unmatched], ["Is that right?"])
    def test_regression_every_q004_question_routes_to_environment(self) -> None:
        # Recorded wall-bracket GROUND row Q-004 asks about solvents, the rail's coating and the bracket's
        # anodising against corrosion; its finish question went to accuracy and the rail one to the rail answer.
        text = recorded_text("events-ground-wall-bracket.jsonl")
        row = next(line for line in text.splitlines() if "Q-004" in line)
        reply = dialogue.respond(case_script("wall-bracket"), row, "fixture")
        self.assertEqual([ask.intent for ask in reply.asks], ["environment", "environment", "environment"])


class GateTests(unittest.TestCase):
    def test_regression_the_recorded_variant_gate_reply_instruction_belongs_to_the_gate(self) -> None:
        # The second small-mechanism fixture build failed as unmatched-at-gate: "Reply with a variant (A, B or C),
        # or ask for a change ..." is the VARIANT gate's own reply instruction, not a separate question.
        reply = dialogue.respond(case_script("small-mechanism"), recorded_text("events-gate-small-mechanism-variant.jsonl"),
                                 "fixture", can_stop=False)
        self.assertEqual([ask.intent for ask in reply.asks], ["variant-gate", "variant-gate"])
        self.assertTrue(reply.at_gate)
        self.assertFalse(reply.unmatched_at_gate)

    def test_a_reply_instruction_with_no_gate_named_before_it_is_not_given_a_gate(self) -> None:
        reply = dialogue.respond(SCRIPT, "Reply with your preferred thread pitch.", "bench")
        self.assertEqual([ask.intent for ask in reply.asks], [None])
    def test_regression_the_recorded_intent_gate_with_a_please_answer_lead_in_is_answered_not_failed(self) -> None:
        # The small-mechanism fixture build failed here: "Please answer:" introduces the numbered asks, and the
        # matcher took it for an unmatched question at the INTENT gate.
        text = recorded_text("events-gate-small-mechanism-intent.jsonl")
        reply = dialogue.respond(case_script("small-mechanism"), text, "fixture", can_stop=False)
        self.assertEqual([(ask.intent, ask.matched) for ask in reply.asks], [("intent-gate", True), ("assumptions", True)])
        self.assertTrue(reply.at_gate)
        self.assertFalse(reply.unmatched_at_gate)
        self.assertIn("petg_print_ream", reply.asks[0].text)
    def test_the_recorded_layout_freeze_prompt_is_a_gate_its_approval_request_matched_through_its_heading(self) -> None:
        text = recorded_text("events-gate-wall-bracket-layout.jsonl")
        reply = dialogue.respond(case_script("wall-bracket"), text, "fixture", can_stop=True)
        self.assertEqual([(ask.text, ask.intent) for ask in reply.asks],
                         [("Approve or name changes. Please also confirm or correct these assumptions:", "layout-freeze")])
        self.assertTrue(reply.at_gate)
        self.assertFalse(reply.unmatched_at_gate)
        self.assertTrue(reply.stops)

    def test_the_recorded_gate_prompt_with_a_question_no_intent_matches_is_unmatched_at_the_gate(self) -> None:
        text = recorded_text("events-gate-wall-bracket-layout.jsonl") + "\nShould the bracket get a drip edge?"
        reply = dialogue.respond(case_script("wall-bracket"), text, "fixture", can_stop=True)
        self.assertTrue(reply.unmatched_at_gate)
        self.assertEqual([ask.text for ask in reply.unmatched], ["Should the bracket get a drip edge?"])

    def test_a_follow_up_to_a_gate_question_is_matched_on_its_own_words_first(self) -> None:
        reply = dialogue.respond(case_script("wall-bracket"), "Do you approve the LAYOUT freeze? Or a finer sweep?", "fixture")
        self.assertEqual([ask.intent for ask in reply.asks], ["layout-freeze", "between-sample"])

    def test_a_question_no_intent_matches_in_a_gate_prompt_is_unmatched_at_the_gate(self) -> None:
        text = "v2 passed the full check.\nDo you approve the LAYOUT freeze? Should the hinge get a detent?"
        reply = dialogue.respond(case_script("print-in-place"), text, "fixture", can_stop=True)
        self.assertTrue(reply.at_gate)
        self.assertTrue(reply.unmatched_at_gate)
        self.assertEqual([ask.text for ask in reply.unmatched], ["Should the hinge get a detent?"])

    def test_an_approval_request_in_a_message_naming_a_gate_is_a_gate_even_when_no_gate_intent_matches(self) -> None:
        text = "The PROVISIONAL gate for v3 is recorded.\nDo I have your approval to continue? Which colour?"
        reply = dialogue.respond(SCRIPT, text, "bench")
        self.assertTrue(reply.at_gate)
        self.assertEqual([ask.text for ask in reply.unmatched], ["Do I have your approval to continue?"])

    def test_who_approves_the_design_is_a_ground_question_not_a_gate(self) -> None:
        reply = dialogue.respond(SCRIPT, "Before the LAYOUT freeze I need to know: who approves the design?", "bench")
        self.assertFalse(reply.at_gate)


class CaseAnswerTests(unittest.TestCase):
    """The committed answers files parse, and their gate and profile questions route as intended."""

    CASE_NAMES = sorted(path.name for path in CASES.iterdir() if path.is_dir() and not path.name.startswith("_"))

    def test_every_case_answers_file_loads_with_both_stop_gates_and_a_profile_intent(self) -> None:
        self.assertEqual(len(self.CASE_NAMES), 5)
        for case in self.CASE_NAMES:
            with self.subTest(case=case):
                script = case_script(case)
                self.assertEqual(dict(script.stop), {"fixture": "layout-freeze", "bench": "parts-gate"})
                self.assertEqual(script.profile_intents(), ("flow-profile",))

    def test_typical_lead_asks_route_to_their_intents(self) -> None:
        script = case_script("wall-bracket")
        cases = [
            ("Do you approve the PARTS gate for v5?", "parts-gate"),
            ("Approve the LAYOUT freeze for v2? The full check passed with no failures.", "layout-freeze"),
            ("Which variant do you select?", "variant-gate"),
            ("Should generated results (STEP, GLB, renders) be stored in git?", "storage"),
            ("Which flow profile should I record?", "flow-profile"),
            ("What loads act on the bracket, and where do they enter?", "loads"),
            ("Is this a prototype or an end-use part?", "service-class"),
            ("What are the rail face width and thickness?", "rail"),
            ("Which machines or bureau will make it?", "equipment"),
            ("Shall I proceed to L2 PARTS?", "proceed"),
        ]
        for ask, intent in cases:
            with self.subTest(ask=ask):
                self.assertEqual(dialogue.classify(script, ask).intent, intent)


if __name__ == "__main__":
    unittest.main()
