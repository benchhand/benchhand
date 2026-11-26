# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""ISO 286 limits of size and fit classification (hole basis).

Usage: uv run --script fits.py SIZE FIT [--expect clearance|transition|interference]

SIZE is the nominal size in mm, 0 < SIZE <= 120. FIT is a hole-basis fit such
as H7/g6, or a single tolerance class (H7, g6). Coverage: hole H and shafts
c, d, f, g, h, js, k, m, n, p, s in grades IT6 to IT11, from the ISO 286-1:2010
tables in ../data/iso286.json (provenance in ../data/README.md).

Hole H: EI = 0, ES = IT. Shafts c-h: es = fundamental deviation, ei = es - IT.
Shafts k-s: ei = fundamental deviation, es = ei + IT. js: +/- IT/2 with
half-micrometre precision, as ISO 286-1:2010 Figure 9 and ISO 286-2:2010 print it.

Fit: max_clearance = ES - ei, min_clearance = EI - es; a negative clearance is
an interference. Clearance fit: min_clearance >= 0. Interference fit:
max_clearance <= 0. Otherwise transition (ISO 286-1:2010 3.3.1-3.3.2).

`declared_fit` reads an interface fit entry (hw/interfaces.yaml limits.press_fit, running_fit,
locating_fit) in exactly one form: this ISO designation, or explicit limits with their nominal_mm and
source (hole and shaft deviations, or an interference or clearance band on the diameter), classified by
the same rule. `compliance: crush_rib` marks the interference designed deformation of a compliant part.
An entry it cannot read raises FitError naming the accepted forms.

stdout: JSON result. stderr: diagnostics.
Exit 0 = computed (and matches --expect), 1 = classification differs from
--expect, 2 = usage error or a size/class outside the covered tables.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data" / "iso286.json"
CLASS_PATTERN = re.compile(r"^(H|c|d|f|g|h|js|k|m|n|p|s)(\d{1,2})$")
FIT_TYPES = ("clearance", "transition", "interference")
MICRON = Decimal("0.001")


class FitError(ValueError):
    """The size or tolerance class is outside the covered tables."""


@cache
def table() -> dict[str, Any]:
    table_data: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return table_data


def _range_index(size: Decimal, bounds: list[int]) -> int:
    if size <= 0:
        raise FitError(f"nominal size must be above 0 mm, got {size}")
    for index, upper in enumerate(bounds):
        if size <= upper:
            return index
    raise FitError(f"nominal size {size} mm is above the covered {bounds[-1]} mm")


def _parse_class(text: str) -> tuple[str, int]:
    match = CLASS_PATTERN.match(text)
    if not match:
        raise FitError(f"unsupported tolerance class {text!r}; covered: H and c d f g h js k m n p s")
    letter, grade = match.group(1), int(match.group(2))
    if str(grade) not in table()["it"]:
        raise FitError(f"grade IT{grade} in {text!r} is outside the covered IT6-IT11")
    return letter, grade


@dataclass(frozen=True)
class Limits:
    """Limit deviations (micrometres) of one tolerance class at one nominal size."""

    designation: str
    is_hole: bool
    size: Decimal
    it: Decimal
    upper: Decimal
    lower: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.designation,
            "IT_um": _num(self.it),
            "upper_deviation_um": _num(self.upper),
            "lower_deviation_um": _num(self.lower),
            "max_mm": _num(self.size + self.upper * MICRON),
            "min_mm": _num(self.size + self.lower * MICRON),
        }


def tolerance_class(size: Decimal, text: str) -> Limits:
    """Limit deviations and limits of size for one tolerance class."""
    data = table()
    letter, grade = _parse_class(text)
    it = Decimal(data["it"][str(grade)][_range_index(size, data["it_upper_bounds_mm"])])
    dev = _range_index(size, data["deviation_upper_bounds_mm"])
    if letter == "H":
        lower = Decimal(0)
        upper = it
    elif letter == "js":
        upper, lower = it / 2, -it / 2
    elif letter in data["shaft_es"]:
        upper = Decimal(data["shaft_es"][letter][dev])
        lower = upper - it
    elif letter == "k":
        column = "grades_4_to_7" if 4 <= grade <= 7 else "other_grades"
        lower = Decimal(data["shaft_ei"]["k"][column][dev])
        upper = lower + it
    else:
        lower = Decimal(data["shaft_ei"][letter][dev])
        upper = lower + it
    return Limits(text, letter == "H", size, it, upper, lower)


def _num(value: Decimal) -> int | float:
    value = value.normalize()
    return int(value) if value == value.to_integral_value() else float(value)


def classify(max_clearance: Decimal, min_clearance: Decimal) -> str:
    if min_clearance >= 0:
        return "clearance"
    if max_clearance <= 0:
        return "interference"
    return "transition"


def evaluate(size: Decimal, fit: str) -> dict[str, Any]:
    """Compute one tolerance class or a hole/shaft fit at a nominal size."""
    parts = fit.split("/")
    if len(parts) > 2 or not all(parts):
        raise FitError(f"fit must be HOLE/SHAFT or one class, got {fit!r}")
    classes = [tolerance_class(size, part) for part in parts]
    result: dict[str, Any] = {"nominal_mm": _num(size), "standard": table()["standard"]}
    if len(classes) == 1:
        only = classes[0]
        result["hole" if only.is_hole else "shaft"] = only.as_dict()
        return result
    hole, shaft = classes
    if not hole.is_hole or shaft.is_hole:
        raise FitError(f"fit {fit!r} must be a hole class H then a shaft class")
    max_clearance = hole.upper - shaft.lower
    min_clearance = hole.lower - shaft.upper
    result.update(
        hole=hole.as_dict(),
        shaft=shaft.as_dict(),
        fit={
            "designation": fit,
            "max_clearance_um": _num(max_clearance),
            "min_clearance_um": _num(min_clearance),
            "type": classify(max_clearance, min_clearance),
        },
    )
    return result


ACCEPTED_FORMS = ("accepted forms: fit: 'SIZE HOLE/SHAFT' (an ISO 286 hole-basis fit such as '22 H7/p6'); or "
                  "nominal_mm and source with one of deviations: {units, hole: {upper, lower}, shaft: {upper, lower}}, "
                  "interference: {min, max, units} or clearance: {min, max, units} on the diameter; compliance: "
                  "crush_rib needs an interference")
EXPLICIT_FORMS = ("deviations", "interference", "clearance")
COMPLIANCE = {"crush_rib": "crush ribs"}
UNITS = {"mm": Decimal(1), "um": MICRON}


def _form_error(reason: str) -> FitError:
    return FitError(f"{reason}; {ACCEPTED_FORMS}")


def _length(value: Any, units: Decimal, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _form_error(f"{what} must be a number, got {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        number = Decimal("NaN")
    if not number.is_finite():
        raise _form_error(f"{what} must be a finite number, got {value!r}")
    return number * units


def _units(entry: Mapping[str, Any], what: str) -> Decimal:
    units = entry.get("units")
    if units not in UNITS:
        raise _form_error(f"{what}.units must be one of {', '.join(UNITS)}, got {units!r}")
    return UNITS[units]


def _span(entry: Any, what: str, low: str, high: str, units: Decimal | None = None) -> tuple[Decimal, Decimal]:
    """(low, high) in mm of a {low, high[, units]} mapping; FitError when low > high."""
    if not isinstance(entry, Mapping):
        raise _form_error(f"{what} must be a mapping with {low} and {high}")
    units = _units(entry, what) if units is None else units
    lower, upper = (_length(entry.get(key), units, f"{what}.{key}") for key in (low, high))
    if lower > upper:
        raise _form_error(f"{what}.{low} {_num(lower)} mm is above {what}.{high} {_num(upper)} mm")
    return lower, upper


def _iso(designation: Any) -> dict[str, Any]:
    if not isinstance(designation, str):
        raise _form_error(f"fit must be a string, got {designation!r}")
    size, _, classes = designation.strip().partition(" ")
    try:
        nominal = Decimal(size)
        if not nominal.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise _form_error(f"fit {designation!r} does not start with a nominal size in mm") from None
    try:
        result = evaluate(nominal, classes.strip())
    except FitError as error:
        raise _form_error(f"fit {designation!r} is not an ISO 286 fit: {error}") from None
    if "fit" not in result:
        raise _form_error(f"fit {designation!r} names one class, not a HOLE/SHAFT fit")
    side = {key: {"upper_mm": _num(result[key]["upper_deviation_um"] * MICRON),
                  "lower_mm": _num(result[key]["lower_deviation_um"] * MICRON)} for key in ("hole", "shaft")}
    return {"form": "iso", "nominal_mm": _num(nominal), "label": designation,
            "max_clearance": Decimal(result["fit"]["max_clearance_um"]) * MICRON,
            "min_clearance": Decimal(result["fit"]["min_clearance_um"]) * MICRON, **side}


def _signed(value: Decimal) -> str:
    return f"{'+' if value > 0 else ''}{_num(value)}"


def _explicit(entry: Mapping[str, Any], form: str) -> dict[str, Any]:
    if not isinstance(entry.get("source"), Mapping) or not isinstance(entry["source"].get("kind"), str):
        raise _form_error("explicit limits need a source {kind, reference}: design intent, datasheet or guideline")
    if "nominal_mm" not in entry:
        raise _form_error("explicit limits need nominal_mm, the diameter they apply to")
    nominal = _length(entry["nominal_mm"], Decimal(1), "nominal_mm")
    if nominal <= 0:
        raise _form_error(f"nominal_mm must be above 0, got {entry['nominal_mm']!r}")
    found: dict[str, Any] = {"form": form, "nominal_mm": _num(nominal)}
    if form == "deviations":
        deviations = entry["deviations"]
        if not isinstance(deviations, Mapping):
            raise _form_error("deviations must be a mapping {units, hole, shaft}")
        units = _units(deviations, "deviations")
        hole, shaft = (_span(deviations.get(side), f"deviations.{side}", "lower", "upper", units) for side in ("hole", "shaft"))
        found.update(max_clearance=hole[1] - shaft[0], min_clearance=hole[0] - shaft[1],
                     label=f"⌀{_num(nominal)} hole {_signed(hole[1])}/{_signed(hole[0])} shaft "
                           f"{_signed(shaft[1])}/{_signed(shaft[0])}",
                     **{side: {"upper_mm": _num(high), "lower_mm": _num(low)}
                        for side, (low, high) in (("hole", hole), ("shaft", shaft))})
    else:
        low, high = _span(entry[form], form, "min", "max")
        clearance = (-high, -low) if form == "interference" else (low, high)
        found.update(min_clearance=clearance[0], max_clearance=clearance[1],
                     label=f"⌀{_num(nominal)} {form} {_num(low)}/{_num(high)}")
    return found


def declared_fit(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The fit of an interface limit (press, running or locating fit), from exactly one form: an ISO 286
    designation `fit: "SIZE HOLE/SHAFT"`, or explicit limits with their `nominal_mm` and `source`, as hole and
    shaft `deviations` or as an `interference` or `clearance` band on the diameter. `compliance: crush_rib`
    declares the interference designed deformation of a compliant part, so it needs one.

    Returns {form, nominal_mm, min_clearance_mm, max_clearance_mm, type, label[, hole, shaft][, compliance, ribs]},
    clearances on the diameter in mm (negative: interference), `type` by `classify`, `hole` and `shaft` their
    {upper_mm, lower_mm} deviations when the form gives them. FitError names the accepted forms."""
    if not isinstance(entry, Mapping):
        raise _form_error(f"a fit must be a mapping, got {entry!r}")
    forms = [key for key in ("fit", *EXPLICIT_FORMS) if key in entry]
    if len(forms) != 1:
        raise _form_error("a fit gives exactly one form, got " + (", ".join(forms) if forms else "none"))
    if forms[0] == "fit":
        if "nominal_mm" in entry or "compliance" in entry:
            raise _form_error("an ISO designation carries its own nominal size and no compliance")
        found = _iso(entry["fit"])
    else:
        found = _explicit(entry, forms[0])
    compliance = entry.get("compliance")
    if compliance is not None and (compliance not in COMPLIANCE or found["min_clearance"] >= 0):
        raise _form_error(f"compliance {compliance!r} must be one of {', '.join(COMPLIANCE)} on a fit with interference")
    if "ribs" in entry and compliance is None:
        raise _form_error("ribs describe a compliance: crush_rib fit")
    max_clearance, min_clearance = found.pop("max_clearance"), found.pop("min_clearance")
    found.update(min_clearance_mm=_num(min_clearance), max_clearance_mm=_num(max_clearance),
                 type=classify(max_clearance, min_clearance))
    if compliance is not None:
        ribs = entry.get("ribs")
        found.update(compliance=compliance, label=f"{found['label']}, {COMPLIANCE[compliance]}")
        if isinstance(ribs, Mapping) and ribs:
            found["ribs"] = dict(ribs)
            shape = " x ".join(str(part) for part in (ribs.get("count"),
                                                      f"{ribs['width_mm']} wide" if "width_mm" in ribs else None) if part)
            found["label"] += f" ({shape})"
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ISO 286 limits of size and fit classification")
    parser.add_argument("size", help="nominal size in mm, 0 < size <= 120")
    parser.add_argument("fit", help="hole-basis fit such as H7/g6, or one class such as g6")
    parser.add_argument("--expect", choices=FIT_TYPES, help="required fit type; exit 1 when it differs")
    args = parser.parse_args(argv)
    try:
        size = Decimal(args.size)
        if not size.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        print(f"fits: size must be a number in mm, got {args.size!r}", file=sys.stderr)
        return 2
    try:
        result = evaluate(size, args.fit)
    except FitError as error:
        print(f"fits: {error}", file=sys.stderr)
        return 2
    if args.expect and "fit" not in result:
        print("fits: --expect needs a HOLE/SHAFT fit", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if args.expect and result["fit"]["type"] != args.expect:
        print(f"fits: {args.fit} at {args.size} mm is {result['fit']['type']}, expected {args.expect}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
