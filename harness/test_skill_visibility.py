# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which `cad-*` skills a user can type as a `/` command in Claude Code.

Claude Code hides a skill from the `/` menu when its front matter says `user-invocable: false`;
the model can still load it. The shared `skills/*/SKILL.md` files carry the key, so the Claude
plugin route, which reads `skills/` directly, hides the helpers too. The key is not an Agent Skills
spec key, so strict validators reject the shared files: an accepted exception. `install.py` strips
the key for every harness but Claude. Only the entry point and two standalone tools stay typable,
so a new skill must be classified here before the suite passes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from modload import load_fresh  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
INSTALLER = ROOT / "harness" / "install.py"
KEY = "user-invocable"
# Agent Skills spec keys, plus the one Claude Code extension the shared files carry on purpose.
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
ALLOWED_KEYS = SPEC_KEYS | {KEY}

# The policy, written out apart from the code under test.
VISIBLE = {"cad-flow", "cad-viewer", "cad-fits"}
HIDDEN = {
    "cad-analysis", "cad-bom", "cad-concept", "cad-design", "cad-drawings", "cad-model",
    "cad-motion", "cad-recheck", "cad-record", "cad-render", "cad-review-dfm", "cad-review-mech",
}


def front_matter(path: Path) -> dict[str, Any]:
    return generate.skill_front_matter(path.read_text(encoding="utf-8"))


def installed_with_key(skills_dir: Path) -> set[str]:
    return {path.parent.name for path in skills_dir.glob("cad-*/SKILL.md") if KEY in front_matter(path)}


class SourceSkillTests(unittest.TestCase):
    def test_every_skill_is_classified_exactly_once(self) -> None:
        names = {path.parent.name for path in SKILLS.glob("cad-*/SKILL.md")}
        self.assertEqual(VISIBLE & HIDDEN, set())
        self.assertEqual(names, VISIBLE | HIDDEN, "classify a new skill in VISIBLE or HIDDEN")

    def test_shared_sources_carry_the_key_on_exactly_the_hidden_skills(self) -> None:
        self.assertEqual(installed_with_key(SKILLS), HIDDEN)
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                fields = front_matter(SKILLS / name / "SKILL.md")
                self.assertIs(fields[KEY], False)
                self.assertNotIn("disable-model-invocation", fields)

    def test_shared_front_matter_uses_only_spec_keys_and_the_claude_exception(self) -> None:
        for path in sorted(SKILLS.glob("cad-*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(set(front_matter(path)), ALLOWED_KEYS)


class InstalledSkillTests(unittest.TestCase):
    def install(self, harness: str) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="cad-visibility-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        result = subprocess.run([sys.executable, str(INSTALLER), "--harness", harness, "--project", str(project)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return next(project.glob("*/skills"))

    def test_claude_install_keeps_the_key_on_exactly_the_hidden_skills(self) -> None:
        skills = self.install("claude")
        self.assertEqual(installed_with_key(skills), HIDDEN)
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                self.assertEqual((skills / name / "SKILL.md").read_bytes(), (SKILLS / name / "SKILL.md").read_bytes())

    def test_other_harness_installs_carry_no_key_and_lose_nothing_else(self) -> None:
        for harness in ("codex", "opencode", "kiro"):
            skills = self.install(harness)
            with self.subTest(harness=harness):
                self.assertEqual(installed_with_key(skills), set())
                for name in sorted(HIDDEN):
                    source = (SKILLS / name / "SKILL.md").read_bytes()
                    installed = (skills / name / "SKILL.md").read_bytes()
                    eol = b"\r\n" if b"\r\n" in source else b"\n"
                    self.assertEqual(installed, source.replace(f"{KEY}: false".encode() + eol, b"", 1))
                    self.assertEqual(set(front_matter(skills / name / "SKILL.md")), {"name", "description"})


def load_install() -> Any:
    return load_fresh("benchhand_install", INSTALLER)


class StripUserInvocableTests(unittest.TestCase):
    def test_removes_only_the_key_line_and_keeps_crlf(self) -> None:
        install = load_install()
        source = b"---\r\nname: cad-x\r\nuser-invocable: false\r\ndescription: d\r\n---\r\nbody\r\nuser-invocable: false\r\n"
        expected = b"---\r\nname: cad-x\r\ndescription: d\r\n---\r\nbody\r\nuser-invocable: false\r\n"
        self.assertEqual(install.strip_user_invocable(source), expected)

    def test_file_without_the_key_is_unchanged(self) -> None:
        install = load_install()
        source = b"---\nname: cad-x\n---\nbody\n"
        self.assertEqual(install.strip_user_invocable(source), source)

    def test_unexpected_forms_fail_loudly(self) -> None:
        install = load_install()
        for front in ("user-invocable: true\n", "user-invocable: false # hidden\n", "user-invocable:  false\n",
                      "user-invocable: false\nuser-invocable: false\n",
                      '"user-invocable": false\n', "'user-invocable': false\n", "user-invocable : false\n"):
            with self.subTest(front=front), self.assertRaises(ValueError):
                install.strip_user_invocable(f"---\nname: x\n{front}---\nbody\n".encode())

    def skill_pair(self, install: Any, harness: str, out: Path) -> Any:
        return next(p for p in install.file_pairs((harness,), out) if p.source.name == "SKILL.md")

    def test_install_keeps_crlf_bytes(self) -> None:
        install = load_install()
        source = b"---\r\nname: cad-x\r\nuser-invocable: false\r\n---\r\nbody\r\n"
        with tempfile.TemporaryDirectory(prefix="cad-crlf-") as temporary:
            root = Path(temporary)
            (root / "skills/cad-x").mkdir(parents=True)
            (root / "skills/cad-x/SKILL.md").write_bytes(source)
            install.SKILLS = root / "skills"
            for harness in ("codex", "opencode", "kiro"):
                with self.subTest(harness=harness):
                    self.assertEqual(self.skill_pair(install, harness, root / "out").content,
                                     source.replace(b"user-invocable: false\r\n", b""))
            self.assertIsNone(self.skill_pair(install, "claude", root / "out").content)

    def test_a_harness_added_later_gets_the_strip_by_default(self) -> None:
        """A synthetic harness the installer has never heard of: an allowlist of today's names would miss it."""
        install = load_install()
        source = b"---\nname: cad-x\nuser-invocable: false\n---\nbody\n"
        with tempfile.TemporaryDirectory(prefix="cad-future-") as temporary:
            root = Path(temporary)
            (root / "skills/cad-x").mkdir(parents=True)
            (root / "skills/cad-x/SKILL.md").write_bytes(source)
            (root / "generated/future/agents").mkdir(parents=True)
            install.SKILLS = root / "skills"
            install.GENERATED = root / "generated"
            install.harness_layout = lambda harness, project, home: (
                project / ".future/skills", project / ".future/agents", None)
            self.assertEqual(self.skill_pair(install, "future", root / "out").content,
                             b"---\nname: cad-x\n---\nbody\n")


CLAUDE_GATE = unittest.skipUnless(shutil.which("claude"), "local smoke test: needs the claude CLI and a logged-in account; CI has neither")


class ClaudeCase(unittest.TestCase):
    """Runs the real `claude` CLI with this checkout loaded as a plugin."""

    def run_claude(self, project: Path, *args: str, max_turns: int = 3) -> list[dict[str, Any]]:
        result = subprocess.run(["claude", "-p", *args, "--output-format", "stream-json", "--verbose",
                                 "--plugin-dir", str(ROOT), "--max-turns", str(max_turns)],
                                cwd=project, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]

    def project(self, prefix: str) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        return project


@CLAUDE_GATE
class PluginRouteTests(ClaudeCase):
    """Local smoke test, not CI coverage: the plugin route reads `skills/` as it is, so the key must hide the helpers."""

    def test_plugin_lists_only_the_visible_skills_and_still_loads_a_hidden_one(self) -> None:
        project = self.project("cad-plugin-")
        events = self.run_claude(project, "hi")
        commands = next(event["slash_commands"] for event in events if event.get("subtype") == "init")
        self.assertEqual({c for c in commands if c.startswith("benchhand:")}, {f"benchhand:{n}" for n in VISIBLE})
        events = self.run_claude(project, "Call the Skill tool with skill 'benchhand:cad-bom', then stop.")
        calls = {block["id"] for event in events if event.get("type") == "assistant"
                 for block in event["message"]["content"]
                 if block.get("type") == "tool_use" and block.get("name") == "Skill"
                 and block["input"].get("skill") == "benchhand:cad-bom"}
        results = [block for event in events if event.get("type") == "user"
                   for block in event["message"]["content"]
                   if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in calls]
        self.assertTrue(calls, "the model never called the Skill tool for cad-bom")
        self.assertTrue(results and not any(block.get("is_error") for block in results), results)


FROZEN_DESIGNATIONS = {"ACME SP-0825-S", "ACME SP-0610-S"}
INTERNAL_PN = re.compile(r"[A-Z]{2,5}-[0-9]{4}")  # the PN grammar in cad-record's assembly and drawing schemas


def report_fields(report: str) -> dict[str, Any]:
    """The structured fields of a researcher report: RESULT, TOOLS, BLOCK_REASON and the `candidates` yaml block."""
    result = re.search(r"^RESULT:\s*(DONE|BLOCKED)\b", report, re.MULTILINE)
    tools_line = re.search(r"^TOOLS:(.*)$", report, re.MULTILINE)
    reason = re.search(r"^BLOCK_REASON:\s*(\w+)", report, re.MULTILINE)
    block = re.search(r"```ya?ml\n(.*?)```", report, re.DOTALL)
    data = yaml.safe_load(block.group(1)) if block else None
    return {
        "result": result.group(1) if result else None,
        "tools": dict(re.findall(r"(web_search|web_fetch)=(yes|no)", tools_line.group(1))) if tools_line else {},
        "block_reason": reason.group(1) if reason else None,
        "candidates": data.get("candidates", []) if isinstance(data, dict) else [],
    }


def candidate_problems(candidates: list[Any], designations: set[str]) -> list[str]:
    """Each way a candidate breaks the report contract: missing field, designation not in the source, or an internal PN."""
    problems = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            problems.append(f"candidate {index} is not a mapping")
            continue
        problems += [f"candidate {index} lacks {key}" for key in ("designation", "order_code", "citation") if not candidate.get(key)]
        if candidate.get("designation") not in designations:
            problems.append(f"candidate {index} designation {candidate.get('designation')!r} is not in the source")
        problems += [f"candidate {index} {key} is an internal PN: {value}" for key, value in candidate.items()
                     if isinstance(value, str) and INTERNAL_PN.fullmatch(value.strip())]
    return problems


FROZEN_DOCUMENT = """# Acme shoulder pin datasheet (frozen copy, rev C, accessed 2026-11-20)

| Manufacturer designation | Shoulder diameter | Double shear rating | Supplier order code |
|---|---|---|---|
| ACME SP-0825-S | 8 mm | 4.2 kN | HV-558201 |
| ACME SP-0610-S | 6 mm | 2.1 kN | HV-558177 |
"""


@CLAUDE_GATE
class ResearcherRouteTests(ClaudeCase):
    """Local smoke tests, not CI coverage: the researcher profile run by stock Claude Code with no web tools.

    The agent runs as the session (`--agent`), so its final message is what the caller gets; this covers
    the fallback path. Delivery through an exposed `SubagentHandback` tool is not tested here.
    """

    AGENT = "benchhand:cad-sourcing-researcher"

    def research(self, project: Path, brief: str) -> str:
        events = self.run_claude(project, brief, "--agent", self.AGENT, "--disallowedTools", "WebSearch", "WebFetch", max_turns=8)
        init = next(event for event in events if event.get("subtype") == "init")
        self.assertFalse({"WebSearch", "WebFetch"} & set(init["tools"]), init["tools"])
        final = next(event for event in reversed(events) if event.get("type") == "result")
        self.assertFalse(final.get("is_error"), final)
        return final["result"]

    def test_a_live_lookup_with_no_web_and_no_frozen_source_is_blocked_for_unavailable_live_evidence(self) -> None:
        fields = report_fields(self.research(self.project("cad-research-a-"), (
            "Unit source-pins. Family: shoulder pins, no PNs yet. Requirement: today's list price and stock for "
            "supplier order code ZX-9981 from the supplier website. The record holds no supplier documents.")))
        self.assertEqual(fields["result"], "BLOCKED")
        self.assertEqual(fields["tools"], {"web_search": "no", "web_fetch": "no"})
        # Missing PNs is `invalid_scope`; this brief has a valid family scope and an unavailable live source.
        self.assertEqual(fields["block_reason"], "live_evidence_unavailable")

    def test_a_brief_without_pns_and_a_frozen_document_returns_candidates_keyed_by_manufacturer_designation(self) -> None:
        project = self.project("cad-research-b-")
        document = project / "supplier" / "acme-shoulder-pins.md"
        document.parent.mkdir()
        document.write_text(FROZEN_DOCUMENT, encoding="utf-8")
        fields = report_fields(self.research(project, (
            "Unit source-pins. Family: shoulder pins, no PNs yet. Requirement: 8 mm shoulder diameter, double shear "
            f"rating of at least 3 kN. The record's frozen supplier document is {document}. It answers the brief; "
            "make no live lookup.")))
        self.assertEqual(fields["result"], "DONE")
        self.assertIsNone(fields["block_reason"])
        self.assertTrue(fields["candidates"], "the report has no candidates block")
        self.assertEqual(candidate_problems(fields["candidates"], FROZEN_DESIGNATIONS), [])
        chosen = [c for c in fields["candidates"] if c["designation"] == "ACME SP-0825-S"]
        self.assertEqual(len(chosen), 1, fields["candidates"])
        self.assertEqual(chosen[0]["order_code"], "HV-558201")
        self.assertIn("acme-shoulder-pins.md", chosen[0]["citation"])


class ReportFieldTests(unittest.TestCase):
    """The report parser and candidate checks that the live tests rely on, run on canned reports."""

    GOOD = (
        "RESULT: DONE\nTOOLS: web_search=no, web_fetch=no\nUnit: source-pins\n\n```yaml\ncandidates:\n"
        "  - designation: ACME SP-0825-S\n    order_code: HV-558201\n    citation: /p/acme.md#row-1\n```\n")

    def test_fields_are_read_from_the_header_lines_and_the_yaml_block(self) -> None:
        fields = report_fields(self.GOOD)
        self.assertEqual(fields["result"], "DONE")
        self.assertEqual(fields["tools"], {"web_search": "no", "web_fetch": "no"})
        self.assertIsNone(fields["block_reason"])
        self.assertEqual(fields["candidates"], [{"designation": "ACME SP-0825-S", "order_code": "HV-558201", "citation": "/p/acme.md#row-1"}])
        self.assertEqual(candidate_problems(fields["candidates"], FROZEN_DESIGNATIONS), [])

    def test_a_blocked_report_carries_its_reason(self) -> None:
        blocked = "RESULT: BLOCKED\nTOOLS: web_search=no, web_fetch=no\nBLOCK_REASON: invalid_scope\n"
        self.assertEqual(report_fields(blocked)["block_reason"], "invalid_scope")
        self.assertEqual(report_fields(blocked)["candidates"], [])

    def test_a_candidate_with_a_schema_valid_internal_pn_or_missing_fields_is_rejected(self) -> None:
        cases = {
            "internal PN as designation": [{"designation": "CAD-0123", "order_code": "HV-558201", "citation": "/p/acme.md"}],
            "internal PN in another field": [{"designation": "ACME SP-0825-S", "order_code": "HV-558201", "citation": "/p/acme.md", "pn": "CAD-0123"}],
            "designation not in the source": [{"designation": "ACME SP-9999-S", "order_code": "HV-558201", "citation": "/p/acme.md"}],
            "no order code": [{"designation": "ACME SP-0825-S", "citation": "/p/acme.md"}],
            "no citation": [{"designation": "ACME SP-0825-S", "order_code": "HV-558201"}],
        }
        for name, candidates in cases.items():
            with self.subTest(case=name):
                self.assertTrue(candidate_problems(candidates, FROZEN_DESIGNATIONS))


if __name__ == "__main__":
    unittest.main()
