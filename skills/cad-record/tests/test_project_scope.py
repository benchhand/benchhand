#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""project_scope.py: the service-class scope of every obligation, one RULES table read by every gate.

The release-scope table in fixtures/release_scope_5dcd2c92.json was frozen from evidence.py SERVICE_POLICY at
5dcd2c92, before the policy moved: every family the record can owe (each PROCESS_CHECKS and _FUNCTION_CHECKS
family, the fixed families of required_checks, one unlisted process kind and one unlisted family), every evidence
level and every service class, with `unknown` for an unrecorded class.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

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


class ProfileTests(unittest.TestCase):
    def test_a_record_without_a_flow_profile_has_none(self) -> None:
        self.assertIsNone(scope.ProjectScope.of(record()).profile)

    def test_the_flow_profile_is_read_as_a_typed_view(self) -> None:
        profile = scope.ProjectScope.of(record(profile=fx.flow_profile(
            human_approval="key", fea={"enabled": False}, critique="light", fdm={"print_in_place": False}))).profile
        self.assertEqual((profile.human_approval, profile.fea, profile.stack_up, profile.critique, profile.fdm_planned,
                          profile.pip), ("key", (False, None), "auto", "light", True, False))


if __name__ == "__main__":
    unittest.main()
