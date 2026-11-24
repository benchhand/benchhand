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


def _is_feature(standard: str) -> bool:
    """Whether `standard` (ISO 965-1, DIN EN ISO 286-2) names a feature standard, which identifies no part."""
    *bodies, number = standard.split()
    root = _SUFFIX.sub("", number)
    return any(body in bodies and root == entry for body, entry in _FEATURE)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _missing(designation: Mapping[str, Any], fields: tuple[str, ...]) -> str | None:
    absent = [f for f in fields if designation.get(f) in (None, "")]
    return f"missing {', '.join(absent)}" if absent else None


def _standard_problem(designation: Mapping[str, Any]) -> str | None:
    problem = _missing(designation, ("standard", "size", "length", "property_class"))
    if problem:
        return problem
    standard = str(designation["standard"])
    if _is_feature(standard):
        return f"{standard} is a feature standard, not a part standard"
    length = designation["length"]
    if length == NOT_APPLICABLE:
        if standard not in NO_LENGTH_STANDARDS:
            return f"{standard} needs a length; not_applicable is for nuts and washers"
    elif not _is_number(length):
        return "length must be a positive number"
    return None


def _family_problem(designation: Mapping[str, Any]) -> str | None:
    family = designation.get("family")
    if family not in KNOWN_FAMILIES:
        return f"family {family!r} is not a known commodity family ({', '.join(sorted(KNOWN_FAMILIES))})"
    fields = KNOWN_FAMILIES[family]
    problem = _missing(designation, fields)
    if problem:
        return problem
    bad = [f for f in fields[1:] if not _is_number(designation[f])]
    return f"{', '.join(bad)} must be a positive number" if bad else None


def designation_problem(designation: Any) -> str | None:
    """Why `designation` does not identify a commodity item, or None when it does."""
    if not isinstance(designation, Mapping):
        return "kind: no structured designation"
    kind = designation.get("kind")
    if kind == "standard_designation":
        return _standard_problem(designation)
    if kind == "family_designation":
        return _family_problem(designation)
    return f"kind {kind!r} is not a commodity designation"


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


def commodity_label(part: Mapping[str, Any]) -> str | None:
    """The order line of a part bought by commodity designation, or None when it is not one: no eligible designation,
    or an identity field the record deliberately leaves open (`procurement.open`), so the item is not settled."""
    designation = commodity_designation(_purchase(part))
    procurement = part.get("procurement")
    opened = procurement.get("open") if isinstance(procurement, Mapping) else None
    if designation is None or opened:
        return None
    return designation_text(designation)
