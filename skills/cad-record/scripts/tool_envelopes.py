# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///
"""The tool entries of assembly operations: what each one reaches, and the shipped envelope of hand work and
common tools, so that no user has to model a hand, a hex key or a wrench. Stdlib only, no file reads.

`classify(name)` keeps the entry's text as written (the instructions read "by hand", "by eye", "3 mm hex key")
and gives it a role:
  - hand: the hand reaches the part (by hand, fingers, finger tight). Envelope: a standard hand, below.
  - observe: nothing reaches (by eye, visual check, clock, wait). No access check.
  - bench: nothing reaches the assembly (bench, on the bench, hold). No access check.
  - tool: a common tool named with its type and size, with a parametric envelope from the cited standard.
  - note: anything else, including a tool type without a size. It stays an instruction note; it is never an
    access obligation, and a cited STEP solid (tool_access envelope) is the way to check a special tool.

An envelope is a stack of coaxial cylinders along the tool axis, from the tip (0 mm, at the work) away from the
work. A cylinder bounds every rotation of the tool about its axis, so the tool's angle about the axis is not
assumed. The sizes are the standards' maximum outlines; a handle that no standard dimensions uses a supplier's
catalogue value, and a value no source gives is an estimate, each named in the envelope's sources:
  - hex key, ISO 2936:2014 Table 1: the long arm engaged along the approach (radius e max / 2, length l1 of the
    standard series), the short arm l2 swept as a disc one key thick at its end;
  - Phillips screwdriver PH1 to PH3, ISO 8764-1:2004 (blade diameter) and ISO 8764-2:2004 Table 1 series B
    (blade length), then a handle from the FACOM ISORYL AP catalogue;
  - open-end, box (ring) and combination wrench, ISO 3318:2016 Table 1 (head width b1 max open end, b2 max box
    end; a combination wrench takes the open end), the head swept about the fastener axis; ISO 3318 gives no
    head thickness, so it is an estimate (0.4 s + 2 mm), and the handle is not covered;
  - square drive socket, ISO 2725-1:2017 Tables 1 to 3 (the larger of d1 max and d2 max over l max, type A), on
    the drive the name gives, else the smallest one the tables list the size for; the ratchet is not covered;
  - engineer's pliers, ISO 5746:2021 Table 1 (jaws l3 by w3 max by t1 max, then the handles' 50 + 15 mm span to
    the nominal length);
  - hand, ISO 15534-3:2000 Table 1: the index finger (breadth a5 P95 23 mm over its length t6 P5 59 mm), then
    the hand behind it (breadth a4 P95 97 mm, to the hand length t4 P5 152 mm).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

HAND_ROLE, OBSERVE, BENCH, TOOL, NOTE = "hand", "observe", "bench", "tool", "note"
ROLES = (HAND_ROLE, OBSERVE, BENCH, TOOL, NOTE)
REACHING = frozenset((HAND_ROLE, TOOL))


@dataclass(frozen=True)
class Segment:
    """One coaxial cylinder: from `start_mm` along the tool axis away from the tip, `length_mm` long."""

    start_mm: float
    length_mm: float
    radius_mm: float


@dataclass(frozen=True)
class Envelope:
    kind: str  # hand, hex_key, phillips_screwdriver, wrench, socket, pliers
    designation: str
    segments: tuple[Segment, ...]
    sources: tuple[dict[str, str], ...]

    def report(self) -> dict[str, Any]:
        return {"kind": self.kind, "designation": self.designation,
                "segments": [{"start_mm": s.start_mm, "length_mm": s.length_mm, "radius_mm": s.radius_mm}
                             for s in self.segments],
                "sources": [dict(source) for source in self.sources]}


@dataclass(frozen=True)
class Entry:
    name: str  # as written, for the instructions
    role: str
    envelope: Envelope | None = None
    note: str | None = None  # why a note has no envelope
    sizes: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------- standard tables (mm)

# ISO 2936:2014 Table 1: width across flats s -> (e max, l1 standard, l2).
HEX_KEYS = {
    0.7: (0.79, 33.0, 7.0), 0.9: (0.99, 33.0, 11.0), 1.3: (1.42, 41.0, 13.0), 1.5: (1.68, 46.5, 15.5),
    2.0: (2.25, 52.0, 18.0), 2.5: (2.82, 58.5, 20.5), 3.0: (3.39, 66.0, 23.0), 3.5: (3.96, 69.5, 25.5),
    4.0: (4.53, 74.0, 29.0), 4.5: (5.10, 80.0, 30.5), 5.0: (5.67, 85.0, 33.0), 6.0: (6.81, 96.0, 38.0),
    7.0: (7.95, 102.0, 41.0), 8.0: (9.09, 108.0, 44.0), 9.0: (10.23, 114.0, 47.0), 10.0: (11.37, 122.0, 50.0),
    11.0: (12.51, 129.0, 53.0), 12.0: (13.65, 137.0, 57.0), 13.0: (14.79, 145.0, 63.0), 14.0: (15.93, 154.0, 70.0),
    15.0: (17.07, 161.0, 73.0), 16.0: (18.21, 168.0, 76.0), 17.0: (19.35, 177.0, 80.0), 18.0: (20.49, 188.0, 84.0),
    19.0: (21.63, 199.0, 89.0), 21.0: (23.91, 211.0, 96.0), 22.0: (25.05, 222.0, 102.0), 23.0: (26.19, 233.0, 108.0),
    24.0: (27.33, 248.0, 114.0), 27.0: (30.75, 277.0, 127.0), 29.0: (33.03, 311.0, 141.0), 30.0: (34.17, 315.0, 142.0),
    32.0: (36.45, 347.0, 157.0), 36.0: (41.01, 391.0, 176.0),
}
# ISO 8764-1:2004 blade diameter, ISO 8764-2:2004 Table 1 series B blade length; handle diameter and length from
# the FACOM ISORYL AP Phillips catalogue (AP.1X75, AP.2X100, AP.3X150).
PHILLIPS = {1: (4.5, 75.0, 24.0, 90.0), 2: (6.0, 100.0, 28.0, 100.0), 3: (8.0, 150.0, 30.0, 110.0)}
# ISO 3318:2016 Table 1: s -> (b1 max open end, b2 max box end).
WRENCHES = {
    3.2: (14.0, 7.0), 4.0: (15.0, 8.0), 5.0: (18.0, 10.0), 5.5: (19.0, 10.5), 6.0: (20.0, 11.5), 7.0: (22.0, 12.5),
    8.0: (24.0, 14.0), 9.0: (25.0, 16.0), 10.0: (28.0, 17.0), 11.0: (30.0, 18.5), 12.0: (31.0, 20.0),
    13.0: (34.0, 21.5), 14.0: (36.0, 23.0), 15.0: (39.0, 24.5), 16.0: (41.0, 26.0), 17.0: (42.0, 27.0),
    18.0: (45.0, 29.0), 19.0: (47.0, 31.0), 20.0: (49.0, 31.5), 21.0: (51.0, 33.5), 22.0: (53.0, 35.0),
    23.0: (55.0, 36.5), 24.0: (57.0, 38.0), 25.0: (59.0, 39.5), 27.0: (64.0, 42.5), 30.0: (70.0, 47.0),
    32.0: (74.0, 50.0), 34.0: (78.0, 53.0), 36.0: (83.0, 56.0), 41.0: (93.0, 63.5), 46.0: (104.0, 71.0),
    50.0: (112.0, 77.0), 55.0: (123.0, 84.5), 60.0: (133.0, 92.0), 65.0: (140.0, 102.0),
}
# ISO 2725-1:2017 Tables 1 to 3: drive square -> s -> (d1 max, d2 max, l max type A).
SOCKETS = {
    6.3: {3.2: (5.9, 12.5, 26.0), 4.0: (6.9, 12.5, 26.0), 5.0: (8.2, 12.5, 26.0), 5.5: (8.8, 12.5, 26.0),
          6.0: (9.4, 12.5, 26.0), 7.0: (11.0, 12.5, 26.0), 8.0: (12.2, 12.5, 26.0), 9.0: (13.5, 13.5, 26.0),
          10.0: (14.7, 14.7, 26.0), 11.0: (16.0, 16.0, 26.0), 12.0: (17.2, 17.2, 26.0), 13.0: (18.5, 18.5, 26.0),
          14.0: (19.7, 19.7, 26.0), 15.0: (21.5, 21.5, 26.0), 16.0: (22.0, 22.0, 26.0), 17.0: (22.5, 22.5, 26.0)},
    10.0: {6.0: (10.1, 20.0, 32.0), 7.0: (11.0, 20.0, 32.0), 8.0: (12.2, 20.0, 32.0), 9.0: (13.5, 20.0, 32.0),
           10.0: (14.7, 20.0, 32.0), 11.0: (16.0, 20.0, 32.0), 12.0: (17.2, 20.0, 32.0), 13.0: (18.5, 20.0, 32.0),
           14.0: (19.7, 24.0, 32.0), 15.0: (21.0, 24.0, 32.0), 16.0: (22.2, 24.0, 35.0), 17.0: (23.5, 24.0, 35.0),
           18.0: (24.7, 24.7, 35.0), 19.0: (26.0, 26.0, 35.0), 20.0: (27.8, 27.8, 35.0), 21.0: (28.5, 28.8, 38.0),
           22.0: (29.7, 29.7, 38.0), 23.0: (31.3, 31.3, 38.0), 24.0: (32.5, 32.5, 38.0), 25.0: (33.0, 33.0, 38.0)},
    12.5: {8.0: (14.0, 24.0, 40.0), 9.0: (14.1, 24.0, 40.0), 10.0: (15.5, 24.0, 40.0), 11.0: (16.7, 24.0, 40.0),
           12.0: (18.0, 24.0, 40.0), 13.0: (19.2, 24.0, 40.0), 14.0: (20.5, 24.0, 40.0), 15.0: (21.7, 24.0, 40.0),
           16.0: (23.0, 25.5, 40.0), 17.0: (24.2, 25.5, 40.0), 18.0: (25.5, 25.5, 42.0), 19.0: (26.7, 26.7, 42.0),
           20.0: (28.0, 28.0, 42.0), 21.0: (29.2, 29.2, 44.0), 22.0: (30.5, 30.5, 44.0), 23.0: (31.3, 31.3, 46.0),
           24.0: (33.0, 33.0, 46.0), 25.0: (33.4, 33.4, 46.0), 27.0: (36.7, 36.7, 48.0), 30.0: (40.5, 40.5, 50.0),
           32.0: (43.0, 43.0, 50.0), 34.0: (46.5, 46.5, 52.0)},
}
# ISO 5746:2021 Table 1, engineer's pliers: nominal length -> (l max, l3 nominal + tolerance, w3 max, t1 max).
PLIERS = {125: (132.0, 31.0, 20.0, 9.0), 140: (149.0, 34.0, 23.0, 10.0), 160: (169.0, 37.0, 25.0, 11.2),
          180: (189.0, 42.0, 28.0, 12.5), 200: (209.0, 48.0, 32.0, 14.0), 220: (234.0, 55.0, 36.0, 16.0),
          250: (264.0, 62.0, 40.0, 18.0)}
PLIERS_HANDLE_SPAN_MM = 65.0  # ISO 5746:2021 Figure 1: 50 +15 -5 mm across the handles
# ISO 15534-3:2000 Table 1.
FINGER_BREADTH_MM, FINGER_LENGTH_MM, HAND_BREADTH_MM, HAND_LENGTH_MM = 23.0, 59.0, 97.0, 152.0
DRIVES = {"1/4": 6.3, "6.3": 6.3, "3/8": 10.0, "10": 10.0, "1/2": 12.5, "12.5": 12.5}


def _standard(reference: str) -> dict[str, str]:
    return {"kind": "standard", "reference": reference}


def _round(value: float) -> float:
    return round(value, 6) + 0.0


def _hand() -> Envelope:
    return Envelope("hand", "hand (index finger and hand)", (
        Segment(0.0, FINGER_LENGTH_MM, FINGER_BREADTH_MM / 2),
        Segment(FINGER_LENGTH_MM, HAND_LENGTH_MM - FINGER_LENGTH_MM, HAND_BREADTH_MM / 2)),
        (_standard("ISO 15534-3:2000 Table 1: index finger breadth a5 P95 23 mm, index finger length t6 P5 59 mm, "
                   "hand breadth at metacarpals a4 P95 97 mm, hand length t4 P5 152 mm"),))


# --------------------------------------------------------------------------- classification

NUMBER = r"(\d+(?:\.\d+)?)"
HAND_TEXT = re.compile(r"(?:by |with (?:the |a |one )?)?(?:hand|hands|finger|fingers|thumb)s?(?:[- ](?:tight|tightened|press(?:ed)?|pressure))?"
                       r"|(?:hand|finger)[- ]tighten(?:ed)?|manual(?:ly)?|press by hand|push by hand")
OBSERVE_TEXT = re.compile(r"(?:by |with (?:the |a )?)?(?:eye|eyes|visual(?: check| inspection)?|look|clock|timer|watch|stopwatch)"
                          r"|visually|wait\b.*|(?:inspect(?:ion)?|check) by eye")
BENCH_TEXT = re.compile(r"(?:on |at )?(?:the |a )?(?:bench|workbench|table)(?: work)?|hold(?: in place)?|bench work")
HEX_KEY = re.compile(NUMBER + r"\s*mm\s+(?:hex(?:agon)?|allen|hex socket screw)(?:\s+socket)?\s+(?:key|wrench)\b"
                     r"|(?:hex(?:agon)?|allen) (?:key|wrench)\s+" + NUMBER + r"\s*mm")
PHILLIPS_TEXT = re.compile(r"\bph\s?([0-9])\b")
WRENCH = re.compile(NUMBER + r"\s*mm\s+(open[- ]?end(?:ed)?|open|ring|box(?:[- ]end)?|combination)?\s*(?:wrench|spanner)\b")
SOCKET = re.compile(NUMBER + r"\s*mm\s+(?:(?:6|12)[- ]point\s+|hex\s+|bi-?hex\s+|double[- ]hex\s+)?socket\b")
DRIVE = re.compile(r"(1/4|3/8|1/2)\s*(?:in(?:ch)?|\")?\s*(?:square\s+)?drive|(6\.3|10|12\.5)\s*mm\s+(?:square\s+)?drive")
PLIERS_TEXT = re.compile(r"(?:(\d{3})\s*mm\s+)?(?:combination |engineer'?s |universal )?pliers\b(?:\s+(\d{3})\s*mm)?")
TOOL_WORDS = re.compile(r"\b(hex|allen|key|screwdriver|driver|wrench|spanner|socket|ratchet|pliers|torx|pozidriv|pz\d)\b")


def _normal(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(\d),(\d)", r"\1.\2", name.strip().lower()))


def _size(table: dict[float, Any], text: str) -> float | None:
    value = float(text)
    return next((size for size in table if math.isclose(size, value, abs_tol=1e-9)), None)


def _hex_key(name: str, match: re.Match[str]) -> Entry:
    size = _size(HEX_KEYS, match.group(1) or match.group(2))
    if size is None:
        return Entry(name, NOTE, note=f"{name!r}: ISO 2936 lists no hex key of that size")
    e_max, long_arm, short_arm = HEX_KEYS[size]
    return Entry(name, TOOL, Envelope("hex_key", f"{size:g} mm hex key", (
        Segment(0.0, long_arm, _round(e_max / 2)), Segment(long_arm, e_max, short_arm)),
        (_standard(f"ISO 2936:2014 Table 1, s {size:g}: e max {e_max} mm, l1 standard {long_arm:g} mm, l2 {short_arm:g} mm"),)))


def _phillips(name: str, match: re.Match[str]) -> Entry:
    size = int(match.group(1))
    if size not in PHILLIPS:
        return Entry(name, NOTE, note=f"{name!r}: no shipped Phillips envelope for PH{size} (PH1 to PH3 are)")
    blade, length, handle, handle_length = PHILLIPS[size]
    return Entry(name, TOOL, Envelope("phillips_screwdriver", f"PH{size} screwdriver", (
        Segment(0.0, length, blade / 2), Segment(length, handle_length, handle / 2)),
        (_standard(f"ISO 8764-1:2004 PH{size} blade diameter {blade:g} mm; ISO 8764-2:2004 Table 1 series B blade length {length:g} mm"),
         {"kind": "supplier", "reference": f"FACOM ISORYL AP Phillips catalogue, PH{size}: handle {handle:g} mm diameter, "
                                           f"{handle_length:g} mm long"})))


def _wrench(name: str, match: re.Match[str]) -> Entry:
    size = _size(WRENCHES, match.group(1))
    if size is None:
        return Entry(name, NOTE, note=f"{name!r}: ISO 3318 lists no wrench of that width across flats")
    form = match.group(2) or "open"
    box = form.startswith(("ring", "box"))
    open_width, box_width = WRENCHES[size]
    width = box_width if box else open_width
    thickness = _round(0.4 * size + 2.0)
    label = "box (ring)" if box else ("combination" if form == "combination" else "open-end")
    return Entry(name, TOOL, Envelope("wrench", f"{size:g} mm {label} wrench", (Segment(0.0, thickness, width / 2),), (
        _standard(f"ISO 3318:2016 Table 1, s {size:g}: {'b2' if box else 'b1'} max {width:g} mm "
                  f"({'box end' if box else 'open end'})"),
        {"kind": "estimate", "reference": f"head thickness {thickness:g} mm (0.4 s + 2 mm): ISO 3318 gives none"})))


def _socket(name: str, text: str, match: re.Match[str]) -> Entry:
    value = float(match.group(1))
    drive_match = DRIVE.search(text)
    drives = [DRIVES[next(g for g in drive_match.groups() if g)]] if drive_match else sorted(SOCKETS)
    for drive in drives:
        size = _size(SOCKETS[drive], str(value))
        if size is not None:
            d1, d2, length = SOCKETS[drive][size]
            return Entry(name, TOOL, Envelope("socket", f"{size:g} mm socket, {drive:g} mm square drive", (
                Segment(0.0, length, max(d1, d2) / 2),),
                (_standard(f"ISO 2725-1:2017, {drive:g} mm square drive, s {size:g}: d1 max {d1:g} mm, d2 max {d2:g} mm, "
                           f"l max type A {length:g} mm"),)))
    return Entry(name, NOTE, note=f"{name!r}: ISO 2725-1 lists no socket of that size on that drive")


def _pliers(name: str, match: re.Match[str]) -> Entry:
    text = match.group(1) or match.group(2)
    if text is None or int(text) not in PLIERS:
        return Entry(name, NOTE, note=f"{name!r}: name the nominal length (one of {', '.join(map(str, PLIERS))} mm) "
                                     "for the ISO 5746 envelope")
    nominal = int(text)
    length, jaws, width, thickness = PLIERS[nominal]
    return Entry(name, TOOL, Envelope("pliers", f"{nominal} mm engineer's pliers", (
        Segment(0.0, jaws, _round(math.hypot(width, thickness) / 2)),
        Segment(jaws, length - jaws, PLIERS_HANDLE_SPAN_MM / 2)),
        (_standard(f"ISO 5746:2021 Table 1, {nominal} mm: l max {length:g} mm, l3 {jaws:g} mm (with tolerance), "
                   f"w3 max {width:g} mm, t1 max {thickness:g} mm; Figure 1 handle span 50 +15 mm"),)))


def classify(name: str) -> Entry:
    """The role of one `tools` or `tool_access` entry, with the shipped envelope of hand work and sized common tools."""
    text = _normal(name)
    if HAND_TEXT.fullmatch(text):
        return Entry(name, HAND_ROLE, _hand())
    if OBSERVE_TEXT.fullmatch(text):
        return Entry(name, OBSERVE)
    if BENCH_TEXT.fullmatch(text):
        return Entry(name, BENCH)
    if match := HEX_KEY.search(text):
        return _hex_key(name, match)
    if match := SOCKET.search(text):
        return _socket(name, text, match)
    if match := WRENCH.search(text):
        return _wrench(name, match)
    if (match := PHILLIPS_TEXT.search(text)) and not re.search(r"\b(bit|socket)\b", text):
        return _phillips(name, match)
    if match := PLIERS_TEXT.search(text):
        return _pliers(name, match)
    if TOOL_WORDS.search(text):
        return Entry(name, NOTE, note=f"{name!r}: a tool without a size or type that has a shipped envelope "
                                     "(for example '3 mm hex key', 'PH2 screwdriver', '8 mm open wrench')")
    return Entry(name, NOTE, note=f"{name!r} is not a recognised tool")
