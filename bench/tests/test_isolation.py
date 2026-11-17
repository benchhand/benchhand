import copy
import json
import sys
import unittest
from pathlib import Path, PurePath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import isolation  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
CAD_SKILLS = frozenset({"cad-analysis", "cad-bom", "cad-concept", "cad-design", "cad-drawings", "cad-fits", "cad-flow",
                        "cad-model", "cad-motion", "cad-recheck", "cad-record", "cad-render", "cad-review-dfm",
                        "cad-review-mech", "cad-viewer"})
CAD_AGENTS = frozenset({"cad-analyst", "cad-clerk", "cad-concept-designer", "cad-dfm-reviewer", "cad-mech-reviewer",
                        "cad-part-designer", "cad-sourcing-researcher"})
# The recorded reports were taken in /scratch/bench-run (paths scrubbed from the run's temp directory).
EXPECTED = isolation.Expected(PurePath("/scratch/bench-run/home"), PurePath("/scratch/bench-run/project"),
                              CAD_SKILLS, CAD_AGENTS, frozenset({"cad-flow"}))


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class RecordedReportTests(unittest.TestCase):
    """Reports recorded from `opencode serve` in a throwaway home, with and without OPENCODE_TEST_HOME."""

    def test_the_isolated_report_shows_only_the_throwaway_home_and_builtins(self) -> None:
        self.assertEqual(isolation.findings(load("isolation-isolated.json"), EXPECTED), [])

    def test_without_the_home_override_the_users_agents_skills_are_reported(self) -> None:
        found = isolation.findings(load("isolation-leaked.json"), EXPECTED)
        self.assertTrue(found)
        self.assertTrue(all(item.startswith("skill loaded from outside the throwaway home: /Users/someone/.agents/")
                            for item in found), found)


class FindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = load("isolation-isolated.json")

    def test_a_config_source_in_the_users_config_is_a_finding(self) -> None:
        report = copy.deepcopy(self.report)
        report["config.get"].append({"type": "document", "path": "/Users/someone/.config/opencode/opencode.json"})
        self.assertEqual(isolation.findings(report, EXPECTED),
                         ["config source outside the throwaway home: /Users/someone/.config/opencode/opencode.json"])

    def test_a_report_that_does_not_list_the_throwaway_config_is_a_finding(self) -> None:
        report = copy.deepcopy(self.report)
        report["config.get"] = [entry for entry in report["config.get"] if entry["type"] != "document"]
        self.assertEqual(len(isolation.findings(report, EXPECTED)), 1)

    def test_an_installed_skill_missing_from_the_report_is_a_finding_not_a_pass(self) -> None:
        report = copy.deepcopy(self.report)
        report["skill.list"]["data"] = [s for s in report["skill.list"]["data"] if s["name"] != "cad-flow"]
        self.assertEqual(isolation.findings(report, EXPECTED), ["installed skill not loaded: cad-flow"])

    def test_an_agent_or_command_the_home_did_not_supply_is_a_finding(self) -> None:
        report = copy.deepcopy(self.report)
        report["agent.list"]["data"].append({"id": "my-reviewer", "name": "my-reviewer"})
        report["command.list"]["data"].append({"name": "ship-it"})
        self.assertEqual(isolation.findings(report, EXPECTED), ["agent the throwaway home did not supply: my-reviewer",
                                                                "command the throwaway home did not supply: ship-it"])

    def test_a_missing_report_fails_instead_of_passing(self) -> None:
        report = {key: value for key, value in self.report.items() if key != "skill.list"}
        self.assertEqual(isolation.findings(report, EXPECTED), ["no skill.list report"])


class SettleTests(unittest.TestCase):
    def test_skills_settle_only_once_all_installed_skills_are_listed_twice_unchanged(self) -> None:
        full = load("isolation-isolated.json")["skill.list"]["data"]
        partial = [skill for skill in full if skill["name"] != "cad-viewer"]
        self.assertFalse(isolation.skills_settled(None, full, EXPECTED))
        self.assertFalse(isolation.skills_settled(partial, partial, EXPECTED))
        self.assertFalse(isolation.skills_settled(partial, full, EXPECTED))
        self.assertTrue(isolation.skills_settled(full, full, EXPECTED))


if __name__ == "__main__":
    unittest.main()
