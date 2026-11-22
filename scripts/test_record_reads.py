#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The record version gate has one door per kind of reader, and no script reads a record around it.

Two checks, both over the AST of every skills/*/scripts/**/*.py (tests excluded):

1. SWEEP. A script that opens a record (calls a YAML loader, or names product.yaml, assembly.yaml,
   interfaces.yaml, requirements.yaml or part.yaml) must name a gated reader: a call or reference to one of GATES.
   The ones that do not are listed in EXEMPT with the reason each may read a record without the gate. An EXEMPT
   entry that no longer reads a record, or now names a gate, fails: the table cannot go stale.
2. OPT-IN. Only the readers that must see a refused record to report it pass `allow_unsupported=True`;
   OPT_INS lists every call site, and the test fails when one is added or removed.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

# The readers that refuse a record of another record_version (see manufacturing_intent.read_documents).
GATES = frozenset({
    "read_documents", "load_record", "check_record", "read_snapshot", "read_folder", "require_supported",
    "require_supported_record", "record_refusal", "built_record", "version_problem", "is_supported_version",
    "record_version",
})
RECORD_FILES = ("product.yaml", "assembly.yaml", "interfaces.yaml", "requirements.yaml", "part.yaml")

# script (below skills/) -> why it may read a record without naming a gate.
EXEMPT: dict[str, str] = {
    "cad-drawings/scripts/drawing_contract.py":
        "runs validate_project first and refuses on any blocking finding, record.version_unsupported among them",
    "cad-flow/scripts/scratch.py": "names hw/parts/<PN>/part.yaml as a path in a delivery check; it opens no record",
    "cad-model/scripts/cadmodel/manufacture.py":
        "takes the Snapshot that cadmodel.record.read_snapshot built, which refuses another version",
    "cad-model/scripts/cadmodel/provenance.py": "names part.yaml only to leave it out of a source hash",
    "cad-record/scripts/decision_records.py": "reads the front matter of decision records, never the product record",
    "cad-record/scripts/guidelines.py": "reads the guideline sets, which are shipped or project data, not the record",
    "cad-record/scripts/sarif.py": "names part.yaml as the location of a finding; it opens no record",
    "cad-record/scripts/validate.py":
        "the reporter: it reads every document of a record to report record.version_unsupported beside the findings",
    "cad-record/scripts/yaml_loader.py": "the shared YAML loader itself; it reads no record on its own",
    "cad-viewer/scripts/cadviewer/gallery.py": "display only: shows the product id as the title and reads nothing else",
    "cad-viewer/scripts/cadviewer/record.py":
        "display only: lists subassembly and part titles for the viewer and reads nothing else of the record",
}

# (script below skills/, enclosing function) of every `allow_unsupported=True`: a reader that reports the refusal.
OPT_INS = frozenset({
    ("cad-record/scripts/impact.py", "read_inputs"),
    ("cad-record/scripts/machine_profile.py", "_read"),
    ("cad-record/scripts/selection.py", "assess_hw"),
})
# `allow_unsupported=<name>`: the readers that hand their caller's choice on.
PASS_THROUGH = frozenset({
    ("cad-record/scripts/manufacturing_intent.py", "load_record"),
    ("cad-record/scripts/manufacturing_intent.py", "check_record"),
})


def scripts() -> list[Path]:
    return sorted(path for path in SKILLS.glob("*/scripts/**/*.py") if "tests" not in path.parts)


def relative(path: Path) -> str:
    return path.relative_to(SKILLS).as_posix()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def reads_a_record(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "safe_load" or (name == "load" and isinstance(func, ast.Attribute)
                                       and getattr(func.value, "id", "") == "yaml"):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(re.search(rf"(^|/){re.escape(name)}$", node.value) for name in RECORD_FILES):
                return True
    return False


def names_a_gate(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in GATES:
            return True
        if isinstance(node, ast.Name) and node.id in GATES:
            return True
        if isinstance(node, ast.alias) and node.name in GATES:
            return True
    return False


class Sweep(unittest.TestCase):
    def test_every_script_that_reads_a_record_goes_through_a_gated_reader(self) -> None:
        ungated = sorted(relative(path) for path in scripts()
                         if reads_a_record(tree := parse(path)) and not names_a_gate(tree) and relative(path) not in EXEMPT)
        self.assertEqual(ungated, [], "read the record through a gated reader (GATES), or add the script to EXEMPT "
                                      "with the reason it needs none")

    def test_the_exempt_table_has_no_stale_entry(self) -> None:
        stale = []
        for name in sorted(EXEMPT):
            path = SKILLS / name
            if not path.is_file():
                stale.append(f"{name}: no such script")
                continue
            tree = parse(path)
            if not reads_a_record(tree):
                stale.append(f"{name}: reads no record")
            elif names_a_gate(tree):
                stale.append(f"{name}: names a gate, so it needs no exemption")
        self.assertEqual(stale, [])

    def test_every_exemption_gives_a_reason(self) -> None:
        self.assertEqual([name for name, reason in EXEMPT.items() if len(reason.split()) < 4], [])


class OptIns(unittest.TestCase):
    def found(self) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        literal: set[tuple[str, str]] = set()
        passed: set[tuple[str, str]] = set()
        for path in scripts():
            tree = parse(path)
            owners: dict[int, str] = {}
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(function):
                        owners[id(node)] = function.name  # the innermost function wins: it is walked last
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "allow_unsupported":
                        continue
                    site = (relative(path), owners.get(id(node), "<module>"))
                    if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        literal.add(site)
                    else:
                        passed.add(site)
        return literal, passed

    def test_the_readers_that_see_a_refused_record_are_exactly_the_listed_ones(self) -> None:
        literal, _ = self.found()
        self.assertEqual(sorted(literal), sorted(OPT_INS))

    def test_only_the_loaders_hand_the_choice_on(self) -> None:
        _, passed = self.found()
        self.assertEqual(sorted(passed), sorted(PASS_THROUGH))


if __name__ == "__main__":
    unittest.main()
