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
assembly_operations = load("cad_record_assembly_operations", SCRIPTS / "assembly_operations.py")
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

    def test_every_canonical_severity_word_is_accepted(self) -> None:
        # the check-packet vocabulary (manufacturing.schema.json severity)
        for word in ("critical", "major", "minor", "info"):
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

    def test_value_takes_a_value_of_any_type(self) -> None:
        for value in (1.5, 0, "62.5 deg", True, [1, "a", None], {"angle_deg": 62.5}, None):
            with self.subTest(value=value):
                self.assertEqual(errors({**GOOD, "value": value}), [])

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

    def test_a_legacy_severity_word_of_validate_or_layout(self) -> None:
        for word in ("error", "warning", "stage"):
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

    def test_an_empty_feature_remedy_source_or_message(self) -> None:
        cases = {
            "subject/feature": mutated(subject={"PN": "TST-0001", "feature": ""}),
            "remedy": mutated(remedy=""),
            "source": mutated(source=""),
            "message": mutated(message=""),
        }
        for where, document in cases.items():
            with self.subTest(field=where):
                self.rejects(document, where)

    def test_a_pointer_that_is_not_text(self) -> None:
        for pointer in (3, ["parts", 0], {"path": "parts/0"}, True):
            with self.subTest(pointer=pointer):
                self.rejects(mutated(pointer=pointer), "pointer")

    def test_an_unknown_top_level_field(self) -> None:
        self.rejects(mutated(severty="major"), "severty")

    def test_a_finding_that_is_not_an_object(self) -> None:
        self.rejects("process.fdm.overhang: too steep", "object")


# --- producers -----------------------------------------------------------------------------------------------
# One entry per finding shape a producer emits. A shape built by a call runs the producer's own code, so it cannot
# drift. A dict literal copies the complete shape the cited lines build, because that producer needs FreeCAD, a
# whole project or the cad-drawings import path.

OPERATIONS = {"id": "fit-bearing", "kind": "press_fit", "part": "TST-0003", "into": "TST-0001", "interface": "IF-001",
              "direction": [0, 0, -2], "acceptance": {"kind": "seated", "features": ["bearing_face", "seat_floor"]},
              "tools": ["arbor press"], "predecessors": ["ghost"]}
[ORDER_FINDING] = assembly_operations.check_order(assembly_operations.normalize({"operations": [OPERATIONS]})).findings

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
    # cad-drawings drawing_intent.py:1480 finding(code, message): bare, no keyword
    "drawing_intent.bare": {"code": "datum_invalid", "message": "geometric tolerance GT-1 uses undeclared datums ['Z']"},
    # drawing_intent.py:106 finding(code, message, feature=...)
    "drawing_intent.with_feature": {
        "code": "fit_unverified", "feature": "pivot_bore",
        "message": "pivot_bore: H7 is a desired limit, and no inspection in TST-0001's route checks pivot_bore as made"},
    # drawing_intent.py:1210 finding(exc.code, message, dimension=...). IntentError.code is invalid_drawing_intent
    "drawing_intent.with_dimension": {"code": "invalid_drawing_intent", "dimension": "d1",
                                      "message": "dimension d1: the request names no feature"},
    # cad-drawings assembly_drawing.py:443 assembly_check.finding(code, message) (defined at assembly_check.py:78)
    "assembly_check.bare": {"code": "invalid_input", "message": "the assembly names no drawing"},
    # assembly_drawing.py:240 finding(exc.code, message, sheet=...). CompositionError code is sheet_no_fit (assembly_sheet.py:170)
    "assembly_check.with_sheet": {"code": "sheet_no_fit", "sheet": 2,
                                  "message": "sheet 2: no allowed sheet fits the assembly views legibly: A3: too wide"},
    # cad-model cadmodel/build.py:82 finding(pn, check, message, kind, where): no code, no severity, `pn` not `PN`
    "build.finding": {"pn": "TST-0001", "check": "build", "type": "ContractError", "message": "no solid", "where": None},
    # cad-design form_check.py:1105 min_wall: not in BLOCKING_CHECKS (:102), so :1143 sets blocking False
    "form_check.min_wall": {"check": "min_wall", "part": "TST-0001", "thickness_mm": 0.4, "location_mm": [1.0, 2.0, 3.0],
                            "min_mm": 0.8, "blocking": False},
    # form_check.py:1082 brep_invalid is in BLOCKING_CHECKS, so blocking is True. It is the only finding with a `source` key
    "form_check.brep_invalid": {"check": "brep_invalid", "part": "TST-0001", "source": "step", "blocking": True},
    # cad-design process_checks/dispatch.py:174-209 _findings() around additive.py:348-360 _unsupported(), an overhang
    # result. The subject comes from Context.subject (core.py:317-323: PN, operation, feature). It also sets obligation and alternatives
    "packet.process_overhang": {
        "id": "f_0123456789abcdef", "code": "process.fdm.overhang", "severity": "major", "outcome": "fail",
        "subject": {"PN": "TST-0001", "operation": "print", "feature": "mount_slot"},
        "measured": {"value": 62.5, "units": "deg"},
        "criterion": {"property": "overhang_angle", "operator": "<=", "value": 45, "units": "deg",
                      "source": {"kind": "policy", "reference": "cad-agent manufacturing-intent dispatch policy (cad-design process_checks)",
                                 "origin": "cad-agent plugin",
                                 "document": "skills/cad-design/scripts/process_checks/__init__.py"}},
        "method": {"name": "sampled down-facing normals", "tool": "cad-design form_check.py process_checks 1"},
        "uncertainty": "sampled normals; edge anchoring approximates bridges",
        "consequence": "a overhang beyond the calibrated unsupported limit with support strategy none sags or fails to print",
        "recheck": {"families": ["process.fdm.support"], "scope": [{"PN": "TST-0001", "operation": "print", "feature": "mount_slot"}],
                    "earliest_stage": "detail"},
        "obligation": "process.fdm.overhang@PN:TST-0001",
        "remedy": "plan supports on reachable non-critical surfaces, or reorient (preferred over splitting)",
        "alternatives": [{"text": "local relief: a chamfer or teardrop under the overhang",
                          "tradeoffs": "changes the geometry; needs the design owner's approval"},
                         {"text": "split the part", "tradeoffs": "adds joints, datums, fasteners, a tolerance stack and load paths"}],
    },
    # cad-record assembly_operations.py:404-426 _finding() via check_order() (cad-motion access.py:339-384 emits the same
    # keys, with severity critical or major): a packet finding about an assembly operation
    "packet.assembly_operation": ORDER_FINDING,
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
    "validate.finding": ["missing code", "severity: enum", "unexpected path"],
    "validate.coded": ["severity: enum", "unexpected action", "unexpected canonical_location", "unexpected path"],
    "manufacturing_intent.problem": ["severity: enum", "unexpected action", "unexpected canonical_location",
                                     "unexpected path"],
    "evidence.finding": ["severity: enum", "unexpected action", "unexpected canonical_location",
                         "unexpected obligation", "unexpected path"],
    "drawing_intent.with_feature": ["unexpected feature"],
    "drawing_intent.with_dimension": ["unexpected dimension"],
    "assembly_check.with_sheet": ["unexpected sheet"],
    "build.finding": ["missing code", "unexpected check", "unexpected pn", "unexpected type", "unexpected where"],
    "form_check.min_wall": ["missing code", "missing message", "unexpected blocking", "unexpected check",
                            "unexpected location_mm", "unexpected min_mm", "unexpected part",
                            "unexpected thickness_mm"],
    "form_check.brep_invalid": ["missing code", "missing message", "unexpected blocking", "unexpected check",
                                "unexpected part"],
    "packet.process_overhang": ["missing message", "subject: unexpected operation", "unexpected alternatives",
                                "unexpected consequence", "unexpected criterion", "unexpected id", "unexpected measured",
                                "unexpected method", "unexpected obligation", "unexpected recheck",
                                "unexpected uncertainty"],
    "packet.assembly_operation": ["missing message", "subject: unexpected assembly_operation", "unexpected consequence",
                                  "unexpected criterion", "unexpected id", "unexpected measured", "unexpected method",
                                  "unexpected obligation", "unexpected recheck", "unexpected uncertainty"],
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
