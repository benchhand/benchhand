#!/usr/bin/env python3
"""The shared finding schema: what a finding may carry, the honest mistakes it rejects, and which of today's
producers already emit it. Every validation goes through the schema registry."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parents[1]
SCHEMAS = SKILL / "schemas"
SCRIPTS = SKILL / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402

registry = load("cad_record_schema_registry", SCRIPTS / "schema_registry.py")
validate = load("cad_record_validate", SCRIPTS / "validate.py")
evidence = load("cad_record_evidence", SCRIPTS / "evidence.py")
SCHEMA_ID = "https://srobroek.com/cad/schemas/finding.schema.json"

GOOD = {
    "code": "process.fdm.overhang",
    "severity": "major",
    "outcome": "fail",
    "subject": {"PN": "TST-0001", "feature": "mount_slot"},
    "location": {"file": "hw/parts/TST-0001/part.yaml", "line": 12},
    "message": "the overhang exceeds the printable angle",
    "remedy": "add a chamfer or split the part",
    "pointer": "drawing/features/mount_slot",
    "value": {"angle_deg": 62.5, "max_deg": 45},
    "source": "step",
}


def errors(document: Any) -> list[str]:
    """The registry validator's messages for `document` against the finding schema."""
    validator = registry.document_validator(SCHEMAS, "finding.schema.json")
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(document)]


def mutated(**changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            item.pop(key)
        else:
            item[key] = value
    return item


class FindingSchema(unittest.TestCase):
    def test_the_schema_is_a_registered_draft_2020_12_document_on_the_shared_base(self) -> None:
        contents = registry.schema(SCHEMAS, "finding.schema.json")
        self.assertEqual(contents["$id"], SCHEMA_ID)
        self.assertEqual(contents["$schema"], "https://json-schema.org/draft/2020-12/schema")
        registry.check_schema(contents)

    def test_a_finding_with_every_field_validates(self) -> None:
        self.assertEqual(errors(GOOD), [])

    def test_only_the_code_and_message_are_required(self) -> None:
        self.assertEqual(errors({"code": "fit_unverified", "message": "no inspection verifies the fit"}), [])

    def test_every_severity_word_a_producer_emits_is_accepted(self) -> None:
        # check-packet vocabulary (manufacturing.schema.json severity), validate.py/layout.json report words
        for word in ("critical", "major", "minor", "info", "error", "warning", "stage"):
            with self.subTest(severity=word):
                self.assertEqual(errors(mutated(severity=word)), [])

    def test_every_outcome_word_of_a_check_is_accepted(self) -> None:
        for word in ("pass", "fail", "unknown", "not_applicable"):
            with self.subTest(outcome=word):
                self.assertEqual(errors(mutated(outcome=word)), [])

    def test_dotted_and_single_segment_codes_are_accepted(self) -> None:
        for code in ("layout.stray", "intent.schema", "motion.play_collision", "section_hatch", "invalid_drawing_intent"):
            with self.subTest(code=code):
                self.assertEqual(errors(mutated(code=code)), [])

    def test_the_schema_examples_include_a_minimal_and_a_full_finding(self) -> None:
        examples = registry.schema(SCHEMAS, "finding.schema.json")["examples"]
        self.assertGreaterEqual(len(examples), 2)
        self.assertTrue(any(set(example) == {"code", "message"} for example in examples))


class HonestMistakes(unittest.TestCase):
    """What an agent gets wrong by hand. Each case names the mistake and the message that points at it."""

    def rejects(self, document: Any, fragment: str) -> None:
        found = errors(document)
        self.assertTrue(found, "accepted")
        self.assertTrue(any(fragment in line for line in found), found)

    def test_a_misspelled_severity(self) -> None:
        for word in ("critcal", "Major", "warn", "fatal", ""):
            with self.subTest(severity=word):
                self.rejects(mutated(severity=word), "severity")

    def test_a_misspelled_outcome(self) -> None:
        self.rejects(mutated(outcome="failed"), "outcome")

    def test_a_missing_code(self) -> None:
        self.rejects(mutated(code=None), "'code' is a required property")

    def test_a_missing_message(self) -> None:
        self.rejects(mutated(message=None), "'message' is a required property")

    def test_a_code_outside_the_pattern(self) -> None:
        for code in ("", "Process.Overhang", "process-overhang", "process..overhang", ".leading", "trailing.", "1stcheck", "two words"):
            with self.subTest(code=code):
                self.rejects(mutated(code=code), "code")

    def test_a_line_given_as_text(self) -> None:
        self.rejects(mutated(location={"file": "hw/product.yaml", "line": "12"}), "location/line")

    def test_a_line_below_one(self) -> None:
        self.rejects(mutated(location={"file": "hw/product.yaml", "line": 0}), "location/line")

    def test_a_location_without_a_file(self) -> None:
        self.rejects(mutated(location={"line": 12}), "'file' is a required property")

    def test_a_location_with_an_empty_file(self) -> None:
        self.rejects(mutated(location={"file": ""}), "location/file")

    def test_a_location_key_misread_from_pointer_and_path(self) -> None:
        self.rejects(mutated(location={"file": "hw/product.yaml", "pointer": "id"}), "location")

    def test_a_subject_with_a_malformed_part_number(self) -> None:
        for pn in ("tst-0001", "TST0001", "TST-1"):
            with self.subTest(PN=pn):
                self.rejects(mutated(subject={"PN": pn}), "subject/PN")

    def test_an_empty_subject(self) -> None:
        self.rejects(mutated(subject={}), "subject")

    def test_a_message_that_is_not_text(self) -> None:
        self.rejects(mutated(message=["a", "b"]), "message")

    def test_an_unknown_top_level_field(self) -> None:
        self.rejects(mutated(severty="major"), "severty")

    def test_a_finding_that_is_not_an_object(self) -> None:
        self.rejects("process.fdm.overhang: too steep", "object")


# --- producers -----------------------------------------------------------------------------------------------
# One entry per finding shape a producer emits. A shape built with `finding(...)` calls the producer's own code.
# A dict literal copies what the cited line builds, because that producer needs FreeCAD or a whole project.

PRODUCERS: dict[str, dict[str, Any]] = {
    # cad-record validate.py:71 finding(): path, message, severity (error|warning|info)
    "validate.finding": validate.finding("hw/product.yaml", "id is required"),
    # validate.py:71 with a code: a layout or design-rule finding adds code, canonical_location and action
    "validate.coded": validate.finding("hw/stray.txt", "a path no layout entry accepts", severity="warning",
                                       code="layout.stray", canonical_location=None, action="move it"),
    # manufacturing_intent.py:236 Problem.finding(): path#/pointer folded into one string
    "manufacturing_intent.problem": validate.intent.Problem(
        "intent.schema", "hw/manufacturing/intent.yaml", "parts/0", "parts/0: 'rev' is a required property").finding(),
    # evidence.py:167 Finding.as_dict()
    "evidence.finding": evidence.Finding("evidence.stale", "hw/checks/v1", "the check is older than the model",
                                         obligation="OBL-1").as_dict(),
    # cad-drawings drawing_intent.py:72 finding(code, message): bare, no location keywords
    "drawing_intent.bare": {"code": "fit_unverified", "message": "bore: H7 is a desired limit and nothing inspects it"},
    # drawing_intent.py:668 finding(code, message, feature=...): `where` keywords ride on the finding
    "drawing_intent.with_feature": {"code": "feature_not_found", "message": "mount_slot is not in the model",
                                    "feature": "mount_slot"},
    # cad-drawings assembly_check.py:78 finding(code, message): bare
    "assembly_check.bare": {"code": "invalid_input", "message": "the assembly names no drawing"},
    # cad-drawings assembly_drawing.py:240 finding(code, message, sheet=...)
    "assembly_check.with_sheet": {"code": "sheet_overflow", "message": "sheet 2: the parts list does not fit", "sheet": 2},
    # cad-model cadmodel/build.py:82 finding(pn, check, message): no code, no severity, `pn` not `PN`
    "build.finding": {"pn": "TST-0001", "check": "build", "type": "ContractError", "message": "no solid", "where": None},
    # cad-design form_check.py:1082 and :1142: `check`, `part`, `blocking` and check-specific numbers, with no message
    "form_check.finding": {"check": "min_wall", "part": "TST-0001", "thickness_mm": 0.4, "location_mm": [1, 2, 3],
                           "min_mm": 0.8, "blocking": True},
    # cad-design process_checks/dispatch.py:189 packet finding (manufacturing.schema.json finding)
    "packet.finding": {
        "id": "obl_1_process_fdm_overhang_1", "code": "process.fdm.overhang", "subject": {"PN": "TST-0001", "feature": "slot"},
        "severity": "major", "outcome": "fail", "measured": {"value": 62.5, "units": "deg"},
        "criterion": {"property": "overhang", "operator": "<=", "value": 45, "units": "deg"},
        "method": {"name": "STEP face normals", "tool": "form_check"}, "uncertainty": "none stated",
        "consequence": "unsupported overhang sags", "remedy": "chamfer it",
        "recheck": {"families": ["feature.fit"], "earliest_stage": "detail"},
    },
    # cad-record assembly_operations.py:409 / cad-motion access.py:356: packet finding on an assembly operation
    "packet.assembly_operation": {
        "id": "op_1_access_unknown", "code": "assembly.access_unknown", "subject": {"assembly_operation": "op_1"},
        "severity": "major", "outcome": "unknown", "message": "access was not measured",
    },
}

# Producers whose emitted shape validates as it is, sorted. A producer that changes shape moves between lists.
CONFORMING = [
    "assembly_check.bare",
    "drawing_intent.bare",
]


def reasons(document: Any) -> list[str]:
    """Stable reasons a document fails: `missing <key>` and `unexpected <key>`, each prefixed by its location."""
    validator = registry.document_validator(SCHEMAS, "finding.schema.json")
    out: list[str] = []
    for error in validator.iter_errors(document):
        where = "/".join(map(str, error.absolute_path))
        prefix = f"{where}: " if where else ""
        if error.validator == "required":
            out.append(f"{prefix}missing {error.message.split(chr(39))[1]}")
        elif error.validator == "additionalProperties":
            extra = sorted(set(error.instance) - set(error.schema["properties"]))
            out.extend(f"{prefix}unexpected {key}" for key in extra)
        else:
            out.append(f"{prefix}{error.validator}")
    return sorted(out)


EXPECTED_REASONS: dict[str, list[str]] = {
    "validate.finding": ["missing code", "unexpected path"],
    "validate.coded": ["unexpected action", "unexpected canonical_location", "unexpected path"],
    "manufacturing_intent.problem": ["unexpected action", "unexpected canonical_location", "unexpected path"],
    "evidence.finding": ["unexpected action", "unexpected canonical_location", "unexpected obligation",
                         "unexpected path"],
    "drawing_intent.with_feature": ["unexpected feature"],
    "assembly_check.with_sheet": ["unexpected sheet"],
    "build.finding": ["missing code", "unexpected check", "unexpected pn", "unexpected type", "unexpected where"],
    "form_check.finding": ["missing code", "missing message", "unexpected blocking", "unexpected check",
                           "unexpected location_mm", "unexpected min_mm", "unexpected part",
                           "unexpected thickness_mm"],
    "packet.finding": ["unexpected consequence", "unexpected criterion", "unexpected id", "unexpected measured",
                       "unexpected method", "unexpected recheck", "unexpected uncertainty", "missing message"],
    "packet.assembly_operation": ["unexpected id", "subject: unexpected assembly_operation"],
}


class ProducerConformance(unittest.TestCase):
    """Which producers already emit the schema, and what each other one needs the migration PR to change."""

    def test_the_conforming_producers_are_exactly_these(self) -> None:
        passing = sorted(name for name, finding in PRODUCERS.items() if not errors(finding))
        self.assertEqual(passing, CONFORMING)

    def test_every_other_producer_fails_for_exactly_the_recorded_reasons(self) -> None:
        for name, finding in PRODUCERS.items():
            if name in CONFORMING:
                continue
            with self.subTest(producer=name):
                self.assertEqual(reasons(finding), sorted(EXPECTED_REASONS[name]))

    def test_every_surveyed_producer_is_either_conforming_or_has_recorded_reasons(self) -> None:
        self.assertEqual(sorted(set(CONFORMING) | set(EXPECTED_REASONS)), sorted(PRODUCERS))
        self.assertEqual(set(CONFORMING) & set(EXPECTED_REASONS), set())


if __name__ == "__main__":
    unittest.main()
