# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which structured designations identify a commodity item, and how a part record spells one.

A commodity item (screw, nut, washer, dowel, O-ring, heat-set insert) is bought by designation: any supplier of
the standard or family will do. The record carries it as `purchase.designation` on the part's purchased route
operation. This module decides whether such a designation is eligible and renders it as one order line. The
process checks, the BOM and the build guide all read the identity through it, so none of them accepts a
designation that another one refuses.

Standard library only: the cad-design, cad-bom and cad-record scripts load it by path."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

# Families no ISO standard defines, each with the fields an order line needs.
KNOWN_FAMILIES: dict[str, tuple[str, ...]] = {
    "heat_set_insert": ("thread", "length", "outside_diameter", "recommended_hole"),
}
# Standards that define a feature (a thread, a fit, a tolerance), not a part. A part standard needs a size that fits it.
FEATURE_STANDARDS = ("ISO 965", "ISO 261", "ISO 262", "ISO 68", "ISO 286", "ISO 2768", "ISO 1101", "ASME Y14.5", "ASME B1.1")
# Part standards of items without a length: nuts and washers.
NO_LENGTH_STANDARDS = frozenset({
    "ISO 4032", "ISO 4033", "ISO 4034", "ISO 4035", "ISO 8673", "ISO 8674", "ISO 8675", "ISO 7040", "ISO 7041",
    "ISO 7089", "ISO 7090", "ISO 7091", "ISO 7092", "ISO 7093-1", "ISO 7094",
    "DIN 934", "DIN 985", "DIN 125", "DIN 9021", "DIN 127",
})
NOT_APPLICABLE = "not_applicable"
_FEATURE = tuple(tuple(entry.split(" ", 1)) for entry in FEATURE_STANDARDS)
_SUFFIX = re.compile(r"(?:-\d+)+$")


# The standards bodies a bought part's designation may name, alone or combined (DIN EN ISO 4017, ISO/EN/DIN 898-1).
STANDARDS = ("DIN", "ISO", "EN", "ASME", "ANSI")

_BODY = "|".join(STANDARDS)
_NUMBER = r"[A-Z]?\d+(?:\.\d+)*(?:-\d+)*"
_TIMES = r"\s*[x\u00d7]\s*\d+(?:\.\d+)?"
# A size is a metric or unified designator (M5, #10-24, M5x20), or a number pair (5x0.6, 1/4-20 x 1). The standard's own
# number alone (DIN 471) is not one.
_FIT = r"(?:\s*[a-zA-Z]{1,2}\d{1,2})?"  # a tolerance class after the diameter: ISO 2338 - 5 m6 x 20
_SIZE = rf"(?:[M#]\d+(?:\.\d+)?(?:-\d+)?(?:{_TIMES})?|\d+(?:[./]\d+)*(?:-\d+)?{_FIT}{_TIMES})(?!\d)"
# A bare nominal size (ISO 7089 - 5 - 200 HV, ASME B18.2.2 1/4-20) follows the standard number after a space, so a part of
# a multi-part standard number (EN 10025-2) is not read as one. It is a whole token: a number, a fraction or a thread
# (1/4-20), an optional series (UNC, UNF, UNEF), then a space, a delimiter or the end. A number before a hardness unit
# (200 HV) is a grade, not a size. A stainless class (A2-70) starts with a letter, so it is never a size.
_HARDNESS_UNIT = r"HV|HB[WS]?|HR[ABC]"
_BARE = (r"\d{1,4}(?:\.\d+)?(?:/\d{1,3})?(?:-\d{1,3})?(?:UNEF|UNC|UNF)?(?=\s|$|[,;)\"])"
         rf"(?!\s*(?:{_HARDNESS_UNIT})\b)")
STANDARD_DESIGNATION = re.compile(
    rf"(?<![A-Za-z])(?P<bodies>(?:{_BODY})(?:[ /]+(?:{_BODY}))*)\s+(?P<number>{_NUMBER})"
    rf"(?:(?:\s*[-\u2013\u2014]\s*|\s+)(?P<size>{_SIZE})|\s+(?:[-\u2013\u2014]\s+)?(?P<bare>{_BARE}))")

# Screw, bolt and nut standards whose size is a thread designation (M6, M6x20, #10-24, 1/4-20). For these a bare number such as
# 8.8 is not a size: it reads as a property class. Each entry names the standard's scope. Every other standard (a washer, a
# ring, a pin) keeps a bare decimal as its size. The list is short and needs completing as more standards are met (#147).
THREADED_FASTENERS = {
    "ISO 4762": "hexagon socket head cap screws, M1.6 to M64",
    "ISO 4014": "hexagon head bolts, product grades A and B",
    "ISO 4017": "hexagon head screws, product grades A and B",
    "ISO 7380": "button head screws with hexagon socket",
    "ISO 10642": "countersunk head screws with hexagon socket",
    "ISO 4032": "hexagon regular nuts (style 1), product grades A and B",
    "DIN 912": "hexagon socket head cap screws",
    "DIN 933": "hexagon head screws, fully threaded",
    "DIN 934": "hexagon nuts",
}
_THREADED = tuple(tuple(entry.split(" ", 1)) for entry in THREADED_FASTENERS)
# A thread designation: M6 (metric, with the coarse pitch as default), or #10-24 and 1/4-20 (which carry threads per inch).
_THREAD_SIZE = re.compile(r"(?:M\d|#\d+-\d|\d+/\d+-\d)")


def _listed(standard_bodies: str, number: str, entries: tuple[tuple[str, ...], ...]) -> bool:
    """Whether the standard (bodies such as DIN EN ISO, and a number such as 965-1) is one of `entries` by body plus number:
    ISO 965 covers ISO 965-1, DIN ISO 965-1 and DIN EN ISO 965-1, not DIN 965 (a screw)."""
    bodies = set(re.split(r"[ /]+", standard_bodies))
    root = _SUFFIX.sub("", number)
    return any(body in bodies and root == entry for body, entry in entries)


def is_feature_standard(standard: str) -> bool:
    """Whether `standard` (ISO 965-1, DIN EN ISO 286-2) names a feature standard, which identifies no part."""
    *bodies, number = standard.split() or [""]
    return _listed(" ".join(bodies), number, _FEATURE)


def _part_standard(match: re.Match[str]) -> bool:
    """A part standard with a size that fits it: a thread designation for a threaded fastener, any size otherwise."""
    bodies, number = match["bodies"], match["number"]
    if _listed(bodies, number, _FEATURE):
        return False
    return not _listed(bodies, number, _THREADED) or _THREAD_SIZE.match(match["size"] or match["bare"]) is not None


def designation_parses(value: Any) -> bool:
    """Whether `value` names a recognized part standard and a size, which identifies the part without an order code."""
    return isinstance(value, str) and any(_part_standard(m) for m in STANDARD_DESIGNATION.finditer(value))


def size_problem(standard: str, size: str) -> str | None:
    """Why `size` does not complete `standard` as a whole size, or None: M4 fits ISO 4762, 8.8 (a property class) does not."""
    text = f"{standard} {size}"
    for match in STANDARD_DESIGNATION.finditer(text):
        if match.start() == 0 and match.end() == len(text) and _part_standard(match):
            return None
    return f"size {size!r} does not fit {standard}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


Issue = tuple[str, str]  # (the designation field, why it is wrong)
_STRING_FIELDS = ("standard", "size", "property_class", "finish", "thread", "material")
_POSITIVE = "must be a positive number"
_STANDARD_ORDER = ("standard", "size", "length", "property_class", "finish")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text_issues(designation: Mapping[str, Any], fields: tuple[str, ...]) -> list[Issue]:
    """Missing or blank required fields and text fields that are not text (YAML reads an unquoted 8.8 as a number)."""
    issues: list[Issue] = []
    for name in fields:
        if _blank(designation.get(name)):
            issues.append((name, "is missing or blank"))
    for name in _STRING_FIELDS:
        value = designation.get(name)
        if name in designation and not _blank(value) and not isinstance(value, str):
            issues.append((name, f"must be text, not {value!r}: quote it so {value!r} stays text"))
    return issues


def _length_issue(designation: Mapping[str, Any]) -> Issue | None:
    length = designation["length"]
    if length == NOT_APPLICABLE:
        if designation["standard"] not in NO_LENGTH_STANDARDS:
            return "length", f"{designation['standard']} needs a length; not_applicable is for nuts and washers"
    elif not _is_number(length):
        return "length", f"{_POSITIVE}, or not_applicable for a nut or washer"
    return None


def _standard_issues(designation: Mapping[str, Any]) -> list[Issue]:
    issues = _text_issues(designation, ("standard", "size", "length", "property_class"))
    named = {field for field, _ in issues}
    if "length" not in named:
        issue = _length_issue({**designation, "standard": str(designation.get("standard", ""))})
        if issue:
            issues.append(issue)
    standard, size = designation.get("standard"), designation.get("size")
    if isinstance(standard, str) and standard.strip():
        if is_feature_standard(standard):
            issues.append(("standard", f"{standard} is a feature standard, not a part standard"))
        elif isinstance(size, str) and size.strip():
            if re.search(r"[x\u00d7]", size):  # the length has its own field: M4 with length 20, never M4x20
                issues.append(("size", f"embeds a length ({size!r}); record the length in the length field"))
            elif size_problem(standard, size):
                issues.append(("size", f"does not fit {standard} ({size!r})"))
    return sorted(issues, key=lambda issue: _STANDARD_ORDER.index(issue[0]) if issue[0] in _STANDARD_ORDER else len(_STANDARD_ORDER))


def _family_issues(designation: Mapping[str, Any]) -> list[Issue]:
    family = designation.get("family")
    if family not in KNOWN_FAMILIES:
        return [("family", f"{family!r} is not a known commodity family ({', '.join(sorted(KNOWN_FAMILIES))})")]
    fields = KNOWN_FAMILIES[family]
    issues = _text_issues(designation, fields)
    named = {field for field, _ in issues}
    return issues + [(f, _POSITIVE) for f in fields[1:] if f not in named and not _is_number(designation[f])]


def designation_issues(designation: Any) -> list[Issue]:
    """Each field of a supplied structured designation that stops it identifying a commodity item, with the reason.
    Empty for an eligible designation and for a value that is no structured designation at all."""
    if not isinstance(designation, Mapping):
        return []
    kind = designation.get("kind")
    if kind == "standard_designation":
        return _standard_issues(designation)
    if kind == "family_designation":
        return _family_issues(designation)
    return [("kind", f"{kind!r} is not a commodity designation")]


def designation_problem(designation: Any) -> str | None:
    """Why `designation` does not identify a commodity item, or None when it does."""
    if not isinstance(designation, Mapping):
        return "kind: no structured designation"
    issues = designation_issues(designation)
    return "; ".join(f"{field} {reason}" for field, reason in issues) or None


def part_designation_issues(part: Mapping[str, Any]) -> list[Issue]:
    """The issues of the structured designation the part's purchased route operation supplies."""
    return designation_issues(_purchase(part).get("designation"))


def _number(value: Any) -> str:
    return f"{value:g}"


def designation_text(designation: Mapping[str, Any]) -> str:
    """One order line: `ISO 4762 M4x20, 8.8, A2` or `heat_set_insert M3x5.7, OD 4.6, hole 4`."""
    if designation.get("kind") == "family_designation":
        parts = [f"{designation['family']} {designation['thread']}x{_number(designation['length'])}",
                 f"OD {_number(designation['outside_diameter'])}", f"hole {_number(designation['recommended_hole'])}"]
        parts += [str(designation[k]) for k in ("material", "finish") if designation.get(k)]
        return ", ".join(parts)
    length = designation["length"]
    head = f"{designation['standard']} {designation['size']}" + ("" if length == NOT_APPLICABLE else f"x{_number(length)}")
    return ", ".join([head, str(designation["property_class"]), *([str(designation["finish"])] if designation.get("finish") else [])])


def _purchase(part: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `purchase` of the part's first purchased route operation, or an empty mapping."""
    intent = part.get("intent")
    route = intent.get("route") if isinstance(intent, Mapping) else None
    for operation in route if isinstance(route, list) else ():
        if isinstance(operation, Mapping) and operation.get("kind") == "purchased" and isinstance(operation.get("purchase"), Mapping):
            return operation["purchase"]
    return {}


def commodity_designation(purchase: Any) -> Mapping[str, Any] | None:
    """The eligible commodity designation of a `purchase` mapping, or None."""
    designation = purchase.get("designation") if isinstance(purchase, Mapping) else None
    return designation if designation_problem(designation) is None else None


def commodity_label(part: Mapping[str, Any], purchase: Any = None) -> str | None:
    """The order line of a part bought by commodity designation, or None when it is not one: no eligible designation,
    or an identity field the record deliberately leaves open (`procurement.open`), so the item is not settled.
    `purchase` is the purchase of a given route operation; the part's first purchased operation otherwise."""
    designation = commodity_designation(_purchase(part) if purchase is None else purchase)
    procurement = part.get("procurement")
    opened = procurement.get("open") if isinstance(procurement, Mapping) else None
    if designation is None or opened:
        return None
    return designation_text(designation)


def designation_cell(part: Mapping[str, Any]) -> str | None:
    """The designation of a part as the BOM writes it and validation expects it: the text the record states in
    `procurement.designation`, else the order line of an eligible commodity designation, else None."""
    procurement = part.get("procurement")
    text = procurement.get("designation") if isinstance(procurement, Mapping) else None
    return text or commodity_label(part)
