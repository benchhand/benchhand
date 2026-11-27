#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""project_scope.py: the service-class scope of every obligation, one RULES table read by every gate.

The release-scope table in fixtures/release_scope_5dcd2c92.json was frozen from evidence.py SERVICE_POLICY at
5dcd2c92, before the policy moved: every fixed family the record can owe (each PROCESS_CHECKS and _FUNCTION_CHECKS
family, the fixed families of required_checks), one representative unlisted process kind and unlisted family, every
schema evidence level and every service class, with `unknown` for an unrecorded class.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

evidence = load("cad_record_evidence", SCRIPTS / "evidence.py")
scope = load("cad_record_project_scope", SCRIPTS / "project_scope.py")
fx = load("cad_record_mi_fixtures", HERE / "fixtures" / "manufacturing_intent.py")
mi = fx.intent_module()
RELEASE_SCOPE = json.loads((HERE / "fixtures" / "release_scope_5dcd2c92.json").read_text(encoding="utf-8"))
STRENGTH = {"family": "engineering.strength", "level": "simulation_supported"}


def record(service_class: str | None = None, profile: dict[str, Any] | None = None) -> Any:
    """The TST record (fixtures.manufacturing_intent.tst_record), with `service_class` known and `profile` as
    product.yaml flow.profile when given."""
    docs = {rel: data for rel, data in fx.tst_record().items() if not rel.startswith("_")}
    if service_class is not None:
        docs["product.yaml"]["use_case"]["service_class"] = fx.known(service_class)
    if profile is not None:
        docs["product.yaml"].setdefault("flow", {})["profile"] = profile
    return mi.normalize_record(docs)


class ReleaseScopeTests(unittest.TestCase):
    def test_release_scope_matches_the_5dcd2c92_service_policy(self) -> None:
        for service_class in RELEASE_SCOPE["classes"]:
            out = set(RELEASE_SCOPE["out_of_scope"][service_class])
            for family in RELEASE_SCOPE["families"]:
                for level in RELEASE_SCOPE["levels"]:
                    with self.subTest(service_class=service_class, family=family, level=level):
                        self.assertEqual(evidence.in_release_scope({"family": family, "level": level}, service_class),
                                         f"{family} {level}" not in out)

    def test_a_level_outside_the_schema_keeps_the_5dcd2c92_scope(self) -> None:
        """At 5dcd2c92 a level outside the schema was in no class's `levels`: out of scope wherever the class named
        its families, in scope from working_prototype on, for an unknown class and for requirement.* and policy.*."""
        owed_from_working_prototype = {"appearance_concept": False, "fit_assembly": False, "functional_demo": False,
                                       "working_prototype": True, "production": True, "safety_critical": True,
                                       "unknown": True, None: True}
        for family in ("interface.acceptance.physicaly_validated", "cad.readback", "unlisted.family"):
            for service_class, owed in owed_from_working_prototype.items():
                with self.subTest(family=family, service_class=service_class):
                    self.assertEqual(evidence.in_release_scope({"family": family, "level": "physicaly_validated"},
                                                               service_class), owed)
        for family in ("requirement.physicaly_validated", "policy.physicaly_validated"):
            for service_class in owed_from_working_prototype:
                with self.subTest(family=family, service_class=service_class):
                    self.assertTrue(evidence.in_release_scope({"family": family, "level": "physicaly_validated"},
                                                              service_class))


class ServiceClassTests(unittest.TestCase):
    def test_an_unknown_or_unlisted_service_class_resolves_every_rule_to_required(self) -> None:
        for service_class in (None, "unknown", "bespoke"):
            for rule in scope.RULES:
                with self.subTest(service_class=service_class, rule=rule.id):
                    self.assertEqual(rule.disposition(service_class), "required")

    def test_a_record_without_a_known_service_class_owes_every_obligation(self) -> None:
        project = scope.ProjectScope.of(record())
        self.assertIsNone(project.service_class)
        self.assertEqual(project.obligation_disposition(STRENGTH), "required")

    def test_the_service_class_is_read_from_the_use_case(self) -> None:
        project = scope.ProjectScope.of(record("fit_assembly"))
        self.assertEqual(project.service_class, "fit_assembly")
        self.assertEqual(project.obligation_disposition(STRENGTH), "out_of_scope")

    def test_every_rule_gives_each_schema_service_class_one_disposition(self) -> None:
        schema = json.loads((HERE.parent / "schemas" / "manufacturing.schema.json").read_text(encoding="utf-8"))
        classes = set(schema["$defs"]["service_class"]["enum"]) - {"unknown"}
        for rule in scope.RULES:
            with self.subTest(rule=rule.id):
                self.assertEqual(set(rule.per_class), classes)
                self.assertLessEqual(set(rule.per_class.values()), set(scope.DISPOSITIONS))


class RulesDigestTests(unittest.TestCase):
    def digest_in_process(self, seed: str, service_class: str) -> str:
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from modload import load_module; "
                "m = load_module('cad_record_project_scope', sys.argv[2]); print(m.rules_sha256(sys.argv[3]))")
        done = subprocess.run([sys.executable, "-B", "-c", code, str(Path(__file__).resolve().parents[3] / "scripts"),
                               str(SCRIPTS / "project_scope.py"), service_class],
                              env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def test_rules_sha256_is_the_same_under_every_hash_seed(self) -> None:
        digests = {self.digest_in_process(seed, "fit_assembly") for seed in ("0", "1", "2")}
        self.assertEqual(digests, {scope.rules_sha256("fit_assembly")})
        self.assertRegex(digests.pop(), r"^[0-9a-f]{64}$")

    def test_rules_sha256_differs_by_service_class(self) -> None:
        classes = (None, "appearance_concept", "fit_assembly", "functional_demo", "working_prototype", "production",
                   "safety_critical")
        self.assertEqual(len({scope.rules_sha256(service_class) for service_class in classes}), len(classes))
        self.assertEqual(scope.ProjectScope.of(record("production")).rules_sha256, scope.rules_sha256("production"))

    def one_rule(self, summary: str, fit_assembly: str = "required") -> tuple[Any, ...]:
        per_class = {cls: "required" for cls in scope.SERVICE_CLASSES} | {"fit_assembly": fit_assembly}
        return (scope.Rule("t.one", scope.Obligations(("x.",), frozenset({"cad_valid"})), per_class, None, summary),)

    def test_rules_sha256_hashes_the_rules_not_only_the_class(self) -> None:
        """Test vectors: sha256 (shasum -a 256) of the canonical JSON of one rule and the class, written out by hand:
        {"rules":[{"applies_to":{"families":["x."],"levels":["cad_valid"]},"id":"t.one","per_class":{...},
        "purpose":null,"summary":"40 mm bore"}],"service_class":"fit_assembly"}, with per_class keys sorted."""
        vectors = {"required": "2ca587568f4618fbac861b0ee8f090d9c6d8f2fd30d9a24497b47f5246a693c9",
                   "out_of_scope": "f53dc87ab0c8f06376016ff170ebd928fa6162f127905a7d75b05cad595602cf"}
        for fit_assembly, digest in vectors.items():
            with self.subTest(fit_assembly=fit_assembly), mock.patch.object(scope, "RULES",
                                                                             self.one_rule("40 mm bore", fit_assembly)):
                self.assertEqual(scope.rules_sha256("fit_assembly"), digest)

    def test_rules_sha256_uses_the_record_canonical_json(self) -> None:
        """layout.canonical_json writes text as UTF-8, not as \\u escapes: the vector above with summary "Ø 40 bore"."""
        with mock.patch.object(scope, "RULES", self.one_rule("Ø 40 bore")):
            self.assertEqual(scope.rules_sha256("fit_assembly"),
                             "308fc5699f28b1694addf46656b4b9a90f2332a845728d3f0bbb4f1a3256217b")


class ProfileTests(unittest.TestCase):
    def test_a_record_without_a_flow_profile_has_none(self) -> None:
        self.assertIsNone(scope.ProjectScope.of(record()).profile)

    def test_the_flow_profile_is_read_as_a_typed_view(self) -> None:
        profile = scope.ProjectScope.of(record(profile=fx.flow_profile(
            human_approval="key", fea={"enabled": False}, critique="light", fdm={"print_in_place": False}))).profile
        self.assertEqual((profile.human_approval, profile.fea, profile.stack_up, profile.critique, profile.fdm_planned,
                          profile.pip), ("key", (False, None), "auto", "light", True, False))


RULES_DIR = HERE / "fixtures" / "rules"


def run_rules(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / "project_scope.py"), "rules", *args],
                          capture_output=True, text=True, check=False)


class RulesCommandTests(unittest.TestCase):
    def test_rules_prints_the_golden_text_for_each_service_class(self) -> None:
        for service_class in scope.SERVICE_CLASSES:
            with self.subTest(service_class=service_class):
                done = run_rules("--class", service_class)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, (RULES_DIR / f"{service_class}.txt").read_text(encoding="utf-8"))
    CLASSES = (*scope.SERVICE_CLASSES, "unknown")
    OTHER = {"id": "unlisted.family@X", "family": "unlisted.family", "level": "cad_valid", "stage": "parts",
             "subject": {"product": True}, "basis": "a family no other rule names"}

    def obligations(self, service_class: str) -> list[dict[str, Any]]:
        """The real obligations of a record of `service_class` (an unrecorded class for `unknown`), plus one for
        obligation.other, which the record never owes."""
        if service_class not in self.cache:
            rec = record(None if service_class == "unknown" else service_class)
            self.cache[service_class] = [o.as_dict() for o in mi.required_checks(rec)] + [self.OTHER]
        return self.cache[service_class]

    cache: dict[str, list[dict[str, Any]]] = {}
    printed: dict[str, str] = {}

    @staticmethod
    def gate(service_class: str, obligation: dict[str, Any], result: str, stage: str) -> list[Any]:
        """The findings of evidence.obligation_gate for one obligation, as the assessment calls it: an unknown class
        is passed as None."""
        return evidence.obligation_gate([obligation], {obligation["id"]: result}, stage,
                                        None if service_class == "unknown" else service_class)

    def rule_line(self, service_class: str, obligation: dict[str, Any]) -> str:
        rule = scope.obligation_rule(obligation).id
        if service_class not in self.printed:
            self.printed[service_class] = run_rules("--class", service_class).stdout
        return next(line for line in self.printed[service_class].splitlines() if line.startswith(f"  {rule} "))

    def test_each_rule_line_matches_the_gates_for_an_unresolved_obligation_of_that_rule(self) -> None:
        """For every class, and every real obligation (product exports included) and one for obligation.other: the
        release blocks it exactly when the line says so, and a gate before the release blocks it once due."""
        for service_class in self.CLASSES:
            for obligation in self.obligations(service_class):
                line = self.rule_line(service_class, obligation)
                release = [f for f in self.gate(service_class, obligation, "unknown", "release") if f.severity == "error"]
                frozen = [f for f in self.gate(service_class, obligation, "unknown", "design_freeze")
                          if f.severity == "error"]
                with self.subTest(service_class=service_class, obligation=obligation["id"]):
                    self.assertEqual("blocks at release until resolved" in line, bool(release))
                    self.assertEqual("not owed at release" in line, not release)
                    self.assertEqual(release == [], scope.obligation_disposition(obligation, None if service_class
                                                                                == "unknown" else service_class)
                                     == "out_of_scope")
                    self.assertTrue(frozen)
                    self.assertIn("blocks once due", line)

    def test_every_rule_is_checked_by_at_least_one_obligation(self) -> None:
        for service_class in self.CLASSES:
            used = {scope.obligation_rule(o).id for o in self.obligations(service_class)}
            self.assertEqual(used, {rule.id for rule in scope.RULES}, service_class)

    def test_a_required_release_error_for_the_product_export_check_is_not_suppressed(self) -> None:
        exports = next(o for o in self.obligations("production") if o["id"] == "cad.product_exports@product")
        findings = self.gate("production", exports, "unknown", "release")
        self.assertEqual([(f.code, f.severity) for f in findings], [("gate.unresolved", "error")])

    def test_a_due_unresolved_check_only_warns_before_layout_and_blocks_from_layout(self) -> None:
        """Header: from LAYOUT on a due unresolved check blocks; before it only warns (intent.use_case at INTENT, for
        every class including unknown)."""
        stages = evidence.STAGES
        for service_class in self.CLASSES:
            for obligation in self.obligations(service_class):
                stage = obligation["stage"]
                if stage == "release":  # the release gate is checked by the rule-line test
                    continue
                expected = "error" if stages.index(stage) >= stages.index("layout") else "warning"
                with self.subTest(service_class=service_class, obligation=obligation["id"]):
                    self.assertEqual({f.severity for f in self.gate(service_class, obligation, "unknown", stage)},
                                     {expected})
        text = run_rules("--class", "unknown").stdout
        self.assertIn("a due check that is unresolved blocks from LAYOUT on, but before LAYOUT only a known failure blocks", text)

    def test_a_planned_physical_check_clears_the_gates_before_the_release_and_not_the_release(self) -> None:
        physical = next(o for o in self.obligations("production") if o["level"] == "physically_validated")
        for service_class in self.CLASSES:
            with self.subTest(service_class=service_class):
                for stage in ("analysis", "design_freeze"):
                    self.assertEqual(self.gate(service_class, physical, "planned", stage), [])
                owed = scope.in_release_scope(physical, None if service_class == "unknown" else service_class)
                self.assertEqual([f.severity for f in self.gate(service_class, physical, "planned", "release")],
                                 ["error"] if owed else [])
        self.assertIn("A planned physical check may stay planned until the release", run_rules().stdout)

    def test_not_assessed_blocks_nothing_at_any_gate_and_the_header_says_so(self) -> None:
        for service_class in self.CLASSES:
            for obligation in self.obligations(service_class):
                with self.subTest(service_class=service_class, obligation=obligation["id"]):
                    for stage in ("analysis", "release"):
                        self.assertFalse([f for f in self.gate(service_class, obligation, "not_assessed", stage)
                                          if f.severity == "error"])
            self.assertIn("A check not assessed is informational at every gate", run_rules("--class", service_class).stdout)

    def test_an_unknown_class_blocks_the_release_by_itself_and_the_output_says_so(self) -> None:
        """Release eligibility, apart from scope: on one assessment whose every obligation is resolved, the release of
        an unknown class still has a blocking finding, and a known class has none for the class."""
        for service_class in self.CLASSES:
            assessment = evidence.assess(record(None if service_class == "unknown" else service_class))
            resolved = dataclasses.replace(assessment, statuses={
                ident: dataclasses.replace(status, result="pass") for ident, status in assessment.statuses.items()})
            blocking = {f.code for f in resolved.release().blocking}
            with self.subTest(service_class=service_class):
                self.assertNotIn("gate.unresolved", blocking)
                self.assertEqual("release.service_class" in blocking, service_class == "unknown")
                self.assertEqual("An unknown service class blocks the release by itself".lower()
                                 in run_rules("--class", service_class).stdout.lower(), service_class == "unknown")

    def test_a_known_failure_blocks_before_layout_and_not_assessed_is_informational_at_every_stage(self) -> None:
        """Exceptions in the header: before LAYOUT a known failure still errors, and not_assessed is an info finding,
        never an error and never a pass."""
        for service_class in self.CLASSES:
            for obligation in self.obligations(service_class):
                stage = obligation["stage"]
                if stage == "release":
                    continue
                with self.subTest(service_class=service_class, obligation=obligation["id"]):
                    self.assertEqual([f.severity for f in self.gate(service_class, obligation, "fail", stage)],
                                     ["error"])
                    self.assertEqual([(f.code, f.severity) for f in
                                      self.gate(service_class, obligation, "not_assessed", stage)],
                                     [("gate.not_assessed", "info")])
        self.assertNotIn("not_assessed", evidence.RESOLVED)
        header = run_rules("--class", "production").stdout
        self.assertIn("before LAYOUT only a known failure blocks and any other unresolved check warns", header)
        self.assertIn("A check not assessed is informational at every gate", header)

    def test_the_first_matching_rule_decides_is_printed(self) -> None:
        self.assertIn("The first rule that matches an obligation decides it", run_rules("--class", "production").stdout)

    EXCEPTION = re.compile(r"^  exception: (\S+) at (.+) is decided by (\S+) \[(\w+)\], not (\S+)"
                           r" \[(\w+)\]$")

    def exceptions(self, service_class: str) -> list[tuple[str, list[str], str, str]]:
        found = []
        for line in run_rules("--class", service_class).stdout.splitlines():
            if line.startswith("  exception:"):
                match = self.EXCEPTION.match(line)
                self.assertIsNotNone(match, line)
                family, levels, rule, disposition = match.group(1, 2, 3, 4)
                found.append((family, list(scope.LEVELS) if levels == "every schema level" else levels.split(", "), rule,
                              disposition))
        return found

    def test_overlapping_families_print_the_rule_and_levels_that_decide_them(self) -> None:
        """For appearance_concept the two optical.route lines differ by level: make owes the CAD, geometry and
        process levels, make_evidence is out of scope above them. Each printed level resolves as printed."""
        lines = run_rules("--class", "appearance_concept").stdout
        self.assertIn("  exception: optical.route at cad_valid, geometrically_manufacturable, process_planned is "
                      "decided by obligation.make [required], not obligation.function [out_of_scope]\n", lines)
        self.assertIn("  exception: optical.route at simulation_supported, physically_validated is decided by "
                      "obligation.make_evidence [out_of_scope], not obligation.function [out_of_scope]\n", lines)
        for service_class in scope.SERVICE_CLASSES:
            exceptions = self.exceptions(service_class)
            self.assertEqual({family for family, *_ in exceptions}, {"optical.route", "motion.play"})
            for family, levels, _rule, disposition in exceptions:
                for level in levels:
                    with self.subTest(service_class=service_class, family=family, level=level):
                        self.assertEqual(scope.obligation_disposition({"family": family, "level": level},
                                                                      service_class), disposition)

    def test_every_level_of_an_overlapping_family_is_printed_once(self) -> None:
        for service_class in scope.SERVICE_CLASSES:
            for family in ("optical.route", "motion.play"):
                printed = [level for fam, levels, *_ in self.exceptions(service_class) if fam == family
                           for level in levels]
                with self.subTest(service_class=service_class, family=family):
                    self.assertCountEqual(printed, scope.LEVELS)

    def test_rules_without_a_class_prints_every_class_in_order(self) -> None:
        done = run_rules()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "\n".join((RULES_DIR / f"{cls}.txt").read_text(encoding="utf-8")
                                                for cls in scope.SERVICE_CLASSES))

    def test_the_unknown_class_owes_every_rule(self) -> None:
        done = run_rules("--class", "unknown")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, (RULES_DIR / "unknown.txt").read_text(encoding="utf-8"))
        rule_lines = [line for line in done.stdout.splitlines() if line.startswith("  obligation.")]
        self.assertEqual(len(rule_lines), len(scope.RULES))
        self.assertTrue(all("[required]" in line for line in rule_lines))

    def test_an_invalid_class_exits_2_and_names_the_choices(self) -> None:
        done = run_rules("--class", "bespoke")
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        for service_class in (*scope.SERVICE_CLASSES, "unknown"):
            self.assertIn(service_class, done.stderr)


if __name__ == "__main__":
    unittest.main()
