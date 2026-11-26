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
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

evidence = load("cad_record_evidence", SCRIPTS / "evidence.py")
RELEASE_SCOPE = json.loads((HERE / "fixtures" / "release_scope_5dcd2c92.json").read_text(encoding="utf-8"))


class ReleaseScopeTests(unittest.TestCase):
    def test_release_scope_matches_the_5dcd2c92_service_policy(self) -> None:
        for service_class in RELEASE_SCOPE["classes"]:
            out = set(RELEASE_SCOPE["out_of_scope"][service_class])
            for family in RELEASE_SCOPE["families"]:
                for level in RELEASE_SCOPE["levels"]:
                    with self.subTest(service_class=service_class, family=family, level=level):
                        self.assertEqual(evidence.in_release_scope({"family": family, "level": level}, service_class),
                                         f"{family} {level}" not in out)


if __name__ == "__main__":
    unittest.main()
