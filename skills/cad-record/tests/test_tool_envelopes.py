#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Tool entries of assembly operations: hand work, visual and bench steps, and common tools need no
modelled envelope (benchhand-c7q.31).

The expected envelope sizes are copied from the cited standards, not from the module:
ISO 2936:2014 Table 1 (hex keys), ISO 8764-1:2004 and ISO 8764-2:2004 Table 1 series B (Phillips
blades), ISO 3318:2016 Table 1 (wrench heads), ISO 15534-3:2000 Table 1 (hand).
Run: python3 -m unittest discover -s skills/cad-record/tests -p test_tool_envelopes.py
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402  modload is reachable only once scripts/ is on sys.path

envelopes = load("cad_record_tool_envelopes", SCRIPTS / "tool_envelopes.py")
ops = load("cad_record_assembly_operations", SCRIPTS / "assembly_operations.py")


def segments(envelope) -> list[tuple[float, float, float]]:
    return [(s.start_mm, s.length_mm, s.radius_mm) for s in envelope.segments]


class ClassificationTests(unittest.TestCase):
    """Each entry keeps its text for the instructions and gets a role for the access check."""

    def test_entries_are_classified_by_what_they_reach(self) -> None:
        cases = {
            "by hand": "hand", "hand": "hand", "fingers": "hand",
            "by eye": "observe", "eye": "observe", "visual check": "observe", "clock": "observe",
            "with a clock": "observe", "wait 10 min": "observe",
            "bench": "bench", "on the bench": "bench", "hold": "bench",
            "3 mm hex key": "tool", "PH2 screwdriver": "tool", "8 mm open wrench": "tool", "13 mm spanner": "tool",
            "frobnicator": "note", "hex key": "note", "torque driver": "note",
        }
        for name, role in cases.items():
            with self.subTest(name=name):
                entry = envelopes.classify(name)
                self.assertEqual(entry.role, role)
                self.assertEqual(entry.name, name, "the instruction text is kept as written")

    def test_a_name_is_read_as_one_tool_phrase_with_its_own_size_and_drive(self) -> None:
        cases = {
            "PH2 alignment marker": ("note", None),
            "Allen key (3 mm)": ("tool", "3 mm hex key"),
            "Inbus key 4 mm": ("tool", "4 mm hex key"),
            "4 mm Allen wrench": ("tool", "4 mm hex key"),
            "hex key, 2.5 mm": ("tool", "2.5 mm hex key"),
            "8 mm socket with 1/2-inch drive": ("tool", "8 mm socket, 12.5 mm square drive"),
            '1/2" drive 8 mm socket': ("tool", "8 mm socket, 12.5 mm square drive"),
            "8 mm socket with 3/4-inch drive": ("note", None),
        }
        for name, (role, designation) in cases.items():
            with self.subTest(name=name):
                entry = envelopes.classify(name)
                self.assertEqual((entry.role, entry.envelope and entry.envelope.designation), (role, designation))
                self.assertEqual(entry.name, name, "the instruction text is kept as written")

    def test_a_socket_on_a_named_drive_takes_that_drive_s_outline(self) -> None:
        # ISO 2725-1:2017 Table 3, 12.5 mm drive, s 8: d1 max 14.0, d2 max 24.0, l max type A 40.
        envelope = envelopes.classify("8 mm socket with 1/2-inch drive").envelope
        self.assertEqual(segments(envelope), [(0.0, 40.0, 12.0)])

    def test_a_metric_drive_is_read_by_its_value_not_its_digits(self) -> None:
        for name in ("10 mm socket, 10 mm drive", "10 mm socket, 10.0 mm drive", "10 mm socket, 10,0 mm drive"):
            with self.subTest(name=name):
                entry = envelopes.classify(name)
                self.assertEqual((entry.role, entry.envelope and entry.envelope.designation),
                                 ("tool", "10 mm socket, 10 mm square drive"))

    def test_only_hand_and_sized_tools_carry_an_envelope(self) -> None:
        for name in ("by eye", "on the bench", "frobnicator", "hex key"):
            with self.subTest(name=name):
                self.assertIsNone(envelopes.classify(name).envelope)


class EnvelopeTests(unittest.TestCase):
    """Parametric envelopes along the tool axis, from the tip (0) away from the work."""

    def test_a_hex_key_has_its_long_arm_engaged_and_its_short_arm_swept(self) -> None:
        envelope = envelopes.classify("3 mm hex key").envelope
        # ISO 2936 size 3: e max 3.39 (radius 1.695), l1 standard 66, l2 23 swept as a disc one key thick.
        self.assertEqual(segments(envelope), [(0.0, 66.0, 1.695), (66.0, 3.39, 23.0)])
        self.assertIn("ISO 2936", envelope.sources[0]["reference"])
        self.assertEqual(envelope.designation, "3 mm hex key")

    def test_a_phillips_screwdriver_has_its_blade_and_a_typical_handle(self) -> None:
        envelope = envelopes.classify("PH2 screwdriver").envelope
        # ISO 8764-1 PH2 blade 6 mm, ISO 8764-2 series B length 100; handle 28 x 100 (FACOM AP catalogue).
        self.assertEqual(segments(envelope), [(0.0, 100.0, 3.0), (100.0, 100.0, 14.0)])
        references = " ".join(source["reference"] for source in envelope.sources)
        self.assertIn("ISO 8764", references)

    def test_a_wrench_head_is_swept_around_the_fastener(self) -> None:
        # ISO 3318 s 8: open end b1 max 24, box end b2 max 14.
        self.assertEqual(envelopes.classify("8 mm open wrench").envelope.segments[0].radius_mm, 12.0)
        self.assertEqual(envelopes.classify("8 mm ring spanner").envelope.segments[0].radius_mm, 7.0)

    def test_the_hand_is_a_finger_then_the_hand_behind_it(self) -> None:
        envelope = envelopes.classify("by hand").envelope
        # ISO 15534-3: index finger breadth a5 P95 23 over its length t6 P5 59; hand breadth a4 P95 97 to the
        # hand length t4 P5 152.
        self.assertEqual(segments(envelope), [(0.0, 59.0, 11.5), (59.0, 93.0, 48.5)])
        self.assertIn("ISO 15534-3", envelope.sources[0]["reference"])

    def test_sized_pliers_have_their_jaws_swept_then_the_rest_to_their_maximum_length(self) -> None:
        # ISO 5746:2021 Table 1, engineer's pliers, nominal 160 mm: l max 169, l3 32 +- 5, w3 max 25, t1 max 11.2
        # (read from the standard, not the module). The jaws, w3 by t1 in section, sweep a circle of their
        # diagonal about the tool axis. Their length is anywhere from 27 to 37 mm, so the wider rest of the tool
        # may start as close as 27 mm to the tip: the jaw section runs to l3 min, and the rest from there to l max.
        jaws_length, jaws_radius, overall = 32.0 - 5.0, round(math.hypot(25.0, 11.2) / 2, 6), 169.0
        for name in ("160 mm combination pliers", "engineer's pliers 160 mm"):
            with self.subTest(name=name):
                envelope = envelopes.classify(name).envelope
                self.assertEqual(envelope.designation, "160 mm engineer's pliers")
                jaws, rest = envelope.segments
                self.assertEqual((jaws.start_mm, jaws.length_mm, jaws.radius_mm), (0.0, jaws_length, jaws_radius))
                self.assertEqual((rest.start_mm, rest.start_mm + rest.length_mm), (jaws_length, overall))
                self.assertIn("ISO 5746:2021 Table 1", envelope.sources[0]["reference"])

    def test_pliers_cover_both_ends_of_the_jaw_length_tolerance(self) -> None:
        # ISO 5746:2021 Table 1 l3: 160 mm 32 +- 5 (27 to 37), 250 mm 50 +- 12 (38 to 62); w3 max and t1 max
        # 25 x 11.2 and 40 x 18; Figure 1 handle span 50 +15 mm. Each obstacle sits inside the l3 band, outside
        # the jaws' swept circle but inside the handles' span: pliers whose jaws end at l3 min hit it, those
        # whose jaws run to l3 max clear it, so the envelope must take it in.
        for nominal, (axial, radial) in {160: (32.0, 20.0), 250: (50.0, 27.0)}.items():
            with self.subTest(nominal=nominal):
                envelope = envelopes.classify(f"{nominal} mm pliers").envelope
                covering = [s.radius_mm for s in envelope.segments if s.start_mm <= axial <= s.start_mm + s.length_mm]
                self.assertGreaterEqual(max(covering), radial)

    def test_pliers_without_a_listed_nominal_length_are_a_note(self) -> None:
        for name in ("pliers", "170 mm pliers"):
            with self.subTest(name=name):
                self.assertEqual(envelopes.classify(name).role, "note")


BRACKET = {"id": "mount-bracket", "kind": "join", "part": "TST-0105", "into": "TST-0101", "direction": [0, 0, -1],
           "acceptance": {"kind": "standoff", "features": ["bracket_foot", "housing_top"]}}
INSPECT = {"id": "inspect-bracket", "kind": "inspect", "part": "TST-0105",
           "acceptance": {"kind": "inspected", "features": ["bracket_foot"]}}


def plan(*items: dict):
    return {request.operation.id: request for request in ops.access_plan(ops.normalize({"operations": list(items)}))}


class AccessPlanTests(unittest.TestCase):
    """What the access check is asked to do with each entry."""

    def test_a_hand_step_reaches_with_the_hand_envelope_along_the_direction(self) -> None:
        request = plan({**BRACKET, "tools": ["by hand"]})["mount-bracket"]
        self.assertEqual(request.unknown, ())
        [reach] = request.reaches
        self.assertEqual((reach.role, reach.source, reach.approach), ("hand", "standard", (0.0, 0.0, -1.0)))

    def test_a_step_that_moves_and_reaches_nothing_is_not_applicable(self) -> None:
        for tools in (["by eye"], ["on the bench", "clock"], ["hold"], []):
            with self.subTest(tools=tools):
                request = plan(BRACKET, {**INSPECT, "tools": tools})["inspect-bracket"]
                self.assertEqual((request.unknown, request.reaches), ((), ()))
                self.assertIn("inspect-bracket moves no part and no tool or hand reaches it", request.not_applicable or "")

    def test_a_common_tool_gets_its_standard_envelope(self) -> None:
        request = plan({**BRACKET, "tools": ["3 mm hex key"]})["mount-bracket"]
        [reach] = request.reaches
        self.assertEqual((reach.source, reach.envelope.designation), ("standard", "3 mm hex key"))
        self.assertEqual(request.unknown, ())

    def test_a_cited_step_solid_overrides_the_standard_envelope(self) -> None:
        cited = {"tool": "3 mm hex key", "approach": [0, 0, -1],
                 "envelope": {"status": "known", "value": "special key", "basis": "supplier_typical",
                              "sources": [{"kind": "supplier", "reference": "key CAD", "artifact": "manufacturing/sources/key.step",
                                           "sha256": "0" * 64}]}}
        [reach] = plan({**BRACKET, "tool_access": [cited]})["mount-bracket"].reaches
        self.assertEqual((reach.source, reach.artifact), ("step", ("manufacturing/sources/key.step", "0" * 64)))

    def test_a_tool_named_in_both_fields_is_checked_once_as_its_tool_access_entry(self) -> None:
        cited = {"tool": "3 mm hex key", "approach": [0, 0, -1],
                 "envelope": {"status": "known", "value": "special key", "basis": "supplier_typical",
                              "sources": [{"kind": "supplier", "reference": "key CAD", "artifact": "manufacturing/sources/key.step",
                                           "sha256": "0" * 64}]}}
        for name in ("3 mm hex key", "3 mm Hex Key"):
            with self.subTest(name=name):
                request = plan({**BRACKET, "tools": [name], "tool_access": [cited]})["mount-bracket"]
                [reach] = request.reaches
                self.assertEqual((reach.index, reach.source), (1, "step"), "the default envelope is not scheduled too")
                self.assertEqual(request.tools[0].name, name, "the instruction text is kept")

    def test_an_unrecognised_name_is_an_instruction_note_not_an_unknown(self) -> None:
        request = plan({**BRACKET, "tools": ["frobnicator"]})["mount-bracket"]
        self.assertEqual((request.unknown, request.reaches), ((), ()))
        self.assertEqual(len(request.notes), 1)
        self.assertIn("frobnicator", request.notes[0])
        self.assertIsNone(request.not_applicable, "the part path is still checked")

    def test_a_declared_special_tool_without_an_established_envelope_stays_unknown(self) -> None:
        # A tool_access entry says the tool must reach; with no shipped envelope and no cited solid, nothing
        # establishes its clearance, so the operation cannot pass on its part path alone.
        placed = {"tool": "bearing puller", "approach": [0, 0, -1], "contact": [30, 0, 44]}
        unknown_envelope = {"status": "unknown", "reason": "no supplier CAD"}
        for label, entry in (("missing", placed), ("explicitly unknown", {**placed, "envelope": unknown_envelope})):
            with self.subTest(envelope=label):
                request = plan({**BRACKET, "tool_access": [entry]})["mount-bracket"]
                self.assertEqual((request.reaches, request.notes, request.not_applicable), ((), (), None))
                [reason] = request.unknown
                self.assertIn("tool 'bearing puller'", reason)
                self.assertIn("envelope", reason)

    def test_a_reaching_tool_without_any_direction_stays_unknown(self) -> None:
        request = plan(BRACKET, {**INSPECT, "kind": "adjust", "id": "adjust-bracket",
                                 "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]},
                                 "tools": ["3 mm hex key"]})["adjust-bracket"]
        self.assertIsNone(request.not_applicable)
        self.assertEqual(request.reaches, ())
        self.assertIn("tool '3 mm hex key' has no approach direction", request.unknown)

    def test_a_shipped_envelope_on_a_part_that_does_not_move_needs_its_contact_point(self) -> None:
        adjust = {"id": "adjust-bracket", "kind": "adjust", "part": "TST-0105",
                  "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]}}
        for entry in ({"tool": "3 mm hex key", "approach": [0, 0, -1]}, {"tool": "by hand", "approach": [0, 0, -1]}):
            with self.subTest(tool=entry["tool"]):
                request = plan(BRACKET, {**adjust, "tool_access": [entry]})["adjust-bracket"]
                self.assertEqual(request.reaches, ())
                [reason] = request.unknown
                self.assertIn(f"tool {entry['tool']!r}", reason)
                self.assertIn("contact", reason)

    def test_a_declared_contact_point_places_the_tool(self) -> None:
        adjust = {"id": "adjust-bracket", "kind": "adjust", "part": "TST-0105",
                  "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]},
                  "tool_access": [{"tool": "3 mm hex key", "approach": [0, 0, -1], "contact": [30, 0, 44]}]}
        request = plan(BRACKET, adjust)["adjust-bracket"]
        [reach] = request.reaches
        self.assertEqual((reach.contact, request.unknown), ((30.0, 0.0, 44.0), ()))

    def test_a_tool_access_entry_stands_for_the_same_tool_written_another_way_in_tools(self) -> None:
        adjust = {"id": "adjust-bracket", "kind": "adjust", "part": "TST-0105",
                  "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]}}
        cases = {"3 mm hex key": "Allen key (3 mm)", "hex key 3 mm": "3mm Inbus key",
                 "10 mm socket on a 3/8\" drive": "10 mm socket, 10 mm drive"}
        for written, declared in cases.items():
            with self.subTest(tools=written, tool_access=declared):
                request = plan(BRACKET, {**adjust, "tools": [written], "tool_access": [
                    {"tool": declared, "approach": [0, 0, -1], "contact": [30, 0, 44]}]})["adjust-bracket"]
                [reach] = request.reaches
                self.assertEqual((reach.index, reach.contact, request.unknown), (1, (30.0, 0.0, 44.0), ()))
                self.assertEqual(request.tools[0].name, written, "the instruction text is kept as written")

    def test_different_tools_in_the_two_fields_are_each_checked(self) -> None:
        adjust = {"id": "adjust-bracket", "kind": "adjust", "part": "TST-0105",
                  "acceptance": {"kind": "adjusted", "features": ["bracket_foot"]}}
        for written, declared in (("4 mm hex key", "3 mm hex key"), ("10 mm socket, 1/2 drive", "10 mm socket, 3/8 drive")):
            with self.subTest(tools=written, tool_access=declared):
                request = plan(BRACKET, {**adjust, "tools": [written], "tool_access": [
                    {"tool": declared, "approach": [0, 0, -1], "contact": [30, 0, 44]}]})["adjust-bracket"]
                self.assertEqual([reach.index for reach in request.reaches], [1])
                self.assertEqual(len(request.unknown), 1, "the tools entry still needs its own placement")
                self.assertIn(repr(written), request.unknown[0])


HOLD = {"id": "hold-screw", "kind": "hold", "part": "TST-0105", "fixture": "bench vice", "orientation": [0, 0, 1],
        "duration": {"status": "known", "value": {"value": 10, "units": "min"}, "basis": "user_declared",
                     "sources": [{"kind": "user", "reference": "TST fixture"}]}}
KEY = {"tool": "3 mm hex key", "approach": [0, 0, -1], "contact": [20, 0, 44]}


class HoldPlanTests(unittest.TestCase):
    """A hold moves no part: it reaches what its tools reach, through the same planner as every other step."""

    def test_a_tool_free_hold_is_not_applicable_with_its_reason(self) -> None:
        for tools in ([], ["by eye"], ["on the bench", "clock"]):
            with self.subTest(tools=tools):
                request = plan(BRACKET, {**HOLD, "tools": tools})["hold-screw"]
                self.assertEqual((request.reaches, request.unknown, request.advisory), ((), (), ()))
                self.assertIn("hold-screw", request.not_applicable or "")

    def test_a_recognised_tool_on_a_hold_is_measured_at_its_own_index(self) -> None:
        cases = {"declared": {"tools": ["spring clamp"], "tool_access": [KEY]},
                 "named, with a contact in tool_access": {"tools": ["spring clamp", "Allen key (3 mm)"], "tool_access": [KEY]}}
        for label, entries in cases.items():
            with self.subTest(label):
                request = plan(BRACKET, {**HOLD, **entries})["hold-screw"]
                [reach] = request.reaches
                index = len(entries["tools"])
                self.assertEqual((reach.index, reach.tool.name, reach.source), (index, "3 mm hex key", "standard"))
                self.assertEqual(request.tools[reach.index].name, "3 mm hex key", "the index is the tool's own")
                self.assertEqual((request.not_applicable, request.unknown, request.advisory), (None, (), ("spring clamp",)))

    def test_a_recognised_tool_on_a_hold_without_its_placement_stays_unknown(self) -> None:
        for entry, needs in (({"tool": "3 mm hex key", "contact": [20, 0, 44]}, "approach"),
                             ({"tool": "3 mm hex key", "approach": [0, 0, -1]}, "contact"),
                             ("by hand", "approach")):
            with self.subTest(needs=needs, entry=entry):
                field = {"tools": [entry]} if isinstance(entry, str) else {"tool_access": [entry]}
                request = plan(BRACKET, {**HOLD, **field})["hold-screw"]
                self.assertEqual((request.reaches, request.not_applicable), ((), None))
                [reason] = request.unknown
                self.assertIn(needs, reason)

    def test_a_hold_whose_tools_are_all_advisory_is_neither_measured_nor_not_applicable(self) -> None:
        for entries in ({"tools": ["spring clamp"]}, {"tool_access": [{"tool": "spring clamp"}]},
                        {"tools": ["spring clamp", "by eye"]}):
            with self.subTest(entries=entries):
                request = plan(BRACKET, {**HOLD, **entries})["hold-screw"]
                self.assertEqual((request.reaches, request.unknown, request.not_applicable), ((), (), None))
                self.assertEqual(request.advisory, ("spring clamp",))


if __name__ == "__main__":
    unittest.main()
