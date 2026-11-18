#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""End to end: the readers that impact and evidence use see a guideline edit, in the current record and in a version
snapshot (benchhand-lch, review P1). Every case names the rule it proves."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_module as load  # noqa: E402

acceptance = load("cad_record_acceptance_fixtures", HERE / "fixtures" / "acceptance.py")
fx, mi, impact = acceptance.fx, acceptance.mi, acceptance.impact
gl = load("cad_record_guidelines", SKILL / "scripts" / "guidelines.py")
build = load("cad_model_record_for_lch", SKILL.parent / "cad-model" / "scripts" / "cadmodel" / "record.py")


def guideline(minimum: float, ident: str = "fdm.generic", revision: int = 1) -> dict[str, Any]:
    cite = fx.source("datasheet", "TST guide")
    return {"schema_version": 1, "id": ident, "revision": revision, "title": "TST", "scope": {"process": "fdm"},
            "limits": [{"characteristic": "min_wall", "operator": ">=", "quantity": fx.quantity(minimum), "citations": [cite]}]}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shipped = self.root / "shipped"
        patch = mock.patch.object(gl, "SHIPPED", self.shipped)
        patch.start()
        self.addCleanup(patch.stop)
        documents = {rel: data for rel, data in fx.tst_record().items() if not rel.startswith("_")}
        self.hw = fx.write_record(self.root / "hw", copy.deepcopy(documents))
        self.path = self.hw / "manufacturing" / "guidelines" / "generic.yaml"
        self.path.parent.mkdir(parents=True)

    def write(self, minimum: float) -> None:
        self.path.write_text(yaml.safe_dump(guideline(minimum), sort_keys=False), encoding="utf-8")

    def ship(self, minimum: float) -> Path:
        self.shipped.mkdir(parents=True, exist_ok=True)
        path = self.shipped / "pa.yaml"
        path.write_text(yaml.safe_dump(guideline(minimum, "fdm.material.pa"), sort_keys=False), encoding="utf-8")
        return path

    def version(self, name: str = "v1") -> Path:
        """A version's record/ folder, written the way a build writes it."""
        pns = sorted(path.parent.name for path in (self.hw / "parts").glob("*/part.yaml"))
        folder = self.root / name / "record"
        build.write_snapshot(folder, build.snapshot(self.hw, pns))
        return folder

    def source_of(self, record: Any) -> str:
        return str(impact.subject_fingerprints(record)["TST-0001"]["source"])


class ProductionReaders(Base):
    def test_the_impact_reader_stales_a_part_when_an_applicable_guideline_is_edited(self) -> None:
        self.write(0.8)
        before = impact.read_record(self.hw)
        self.write(0.9)
        after = impact.read_record(self.hw)
        changed = impact.changed_domains(impact.subject_fingerprints(before), impact.subject_fingerprints(after))
        self.assertIn("source", changed.get("TST-0001", ()))

    def test_the_evidence_reader_carries_the_guideline_digests(self) -> None:
        self.write(0.8)
        _documents, record = impact.read_inputs(self.hw)
        self.assertEqual(list(record.guideline_digests["tst_printer"]), ["fdm.generic@1"])


class VersionSnapshots(Base):
    def test_a_build_copies_the_winning_sets_byte_identically(self) -> None:
        self.write(0.8)
        shipped = self.ship(1.5)
        hidden = self.shipped / "generic.yaml"
        hidden.write_text(yaml.safe_dump(guideline(0.1, "fdm.generic", 9), sort_keys=False), encoding="utf-8")
        folder = self.version()
        found = {path.relative_to(folder).as_posix(): path.read_bytes() for path in (folder / "manufacturing" / "guidelines").glob("*.yaml")}
        self.assertEqual(found, {"manufacturing/guidelines/generic.yaml": self.path.read_bytes(),
                                 "manufacturing/guidelines/shipped--pa.yaml": shipped.read_bytes()},
                         "the project set shadows the shipped generic set, so only its own bytes are copied")

    def test_a_version_build_with_no_edit_stales_nothing(self) -> None:
        self.write(0.8)
        self.ship(1.5)
        folder = self.version()
        _documents, built = impact.read_inputs(folder, ledgers_from=self.hw)
        _documents, current = impact.read_inputs(self.hw)
        self.assertEqual(self.source_of(built), self.source_of(current))
        self.assertEqual(dict(built.guideline_digests["tst_printer"]), dict(current.guideline_digests["tst_printer"]))

    def test_editing_a_project_set_after_the_build_stales_the_part(self) -> None:
        self.write(0.8)
        folder = self.version()
        self.write(0.9)
        _documents, built = impact.read_inputs(folder, ledgers_from=self.hw)
        _documents, current = impact.read_inputs(self.hw)
        changed = impact.changed_domains(impact.subject_fingerprints(built), impact.subject_fingerprints(current))
        self.assertIn("source", changed.get("TST-0001", ()))

    def test_editing_a_shipped_set_after_the_build_stales_the_part(self) -> None:
        self.ship(1.5)
        folder = self.version()
        self.ship(1.6)
        _documents, built = impact.read_inputs(folder, ledgers_from=self.hw)
        _documents, current = impact.read_inputs(self.hw)
        changed = impact.changed_domains(impact.subject_fingerprints(built), impact.subject_fingerprints(current))
        self.assertIn("source", changed.get("TST-0001", ()))

    def test_an_edited_shipped_copy_in_the_snapshot_stales_the_part(self) -> None:
        self.ship(1.5)
        folder = self.version()
        copy = folder / "manufacturing" / "guidelines" / "shipped--pa.yaml"
        copy.write_text(yaml.safe_dump(guideline(1.9, "fdm.material.pa"), sort_keys=False), encoding="utf-8")
        _documents, built = impact.read_inputs(folder, ledgers_from=self.hw)
        _documents, current = impact.read_inputs(self.hw)
        changed = impact.changed_domains(impact.subject_fingerprints(built), impact.subject_fingerprints(current))
        self.assertIn("source", changed.get("TST-0001", ()))

    def test_a_snapshot_is_read_only_from_its_own_copies(self) -> None:
        folder = self.version()
        self.ship(1.5)
        _documents, built = impact.read_inputs(folder, ledgers_from=self.hw)
        self.assertEqual(dict(built.guideline_digests), {}, "a shipped set added after the build is not part of the version")


if __name__ == "__main__":
    unittest.main()
