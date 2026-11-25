# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The record's units and quantities: the one parser of a quantity's schema form, shared by manufacturing_intent
(which re-exports every name here) and by modules that must stay free of its YAML and schema dependencies, such as
assembly_operations. Standard library only; FreeCAD's Python 3.11 loads it through manufacturing_intent."""

from __future__ import annotations

import math
import types
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class IntentError(ValueError):
    """A call the record modules refuse: an unknown PN, a cyclic route, an unknown property or unit
    (manufacturing_intent.IntentError is this class)."""


# Units: name -> (dimension, scale to the dimension's base unit, offset). base = value * scale + offset.
UNITS: Mapping[str, tuple[str, float, float]] = types.MappingProxyType({
    "mm": ("length", 1e-3, 0.0), "um": ("length", 1e-6, 0.0), "m": ("length", 1.0, 0.0),
    "mm2": ("area", 1e-6, 0.0), "mm3": ("volume", 1e-9, 0.0),
    "deg": ("angle", math.pi / 180.0, 0.0), "rad": ("angle", 1.0, 0.0),
    "N": ("force", 1.0, 0.0), "kN": ("force", 1e3, 0.0),
    "N_mm": ("torque", 1e-3, 0.0), "N_m": ("torque", 1.0, 0.0),
    "MPa": ("stress", 1e6, 0.0), "GPa": ("stress", 1e9, 0.0), "bar": ("stress", 1e5, 0.0),
    "g": ("mass", 1e-3, 0.0), "kg": ("mass", 1.0, 0.0),
    "kg_m3": ("mass_density", 1.0, 0.0), "g_cm3": ("mass_density", 1e3, 0.0),
    "deg_C": ("temperature", 1.0, 273.15), "K": ("temperature", 1.0, 0.0),
    "s": ("time", 1.0, 0.0), "min": ("time", 60.0, 0.0), "h": ("time", 3600.0, 0.0), "d": ("time", 86400.0, 0.0),
    "cycles": ("count", 1.0, 0.0), "count": ("count", 1.0, 0.0),
    "rpm": ("frequency", 1.0 / 60.0, 0.0), "Hz": ("frequency", 1.0, 0.0),
    "mm_s": ("speed", 1e-3, 0.0), "m_s": ("speed", 1.0, 0.0),
    "percent": ("ratio", 0.01, 0.0), "dimensionless": ("ratio", 1.0, 0.0),
    "J": ("energy", 1.0, 0.0), "J_m": ("energy_per_length", 1.0, 0.0), "kJ_m2": ("energy_per_area", 1e3, 0.0),
    "W": ("power", 1.0, 0.0), "kW": ("power", 1e3, 0.0),
    "W_m_K": ("thermal_conductivity", 1.0, 0.0),
    "um_m_K": ("thermal_expansion", 1e-6, 0.0), "per_K": ("thermal_expansion", 1.0, 0.0),
})


def _unknown_unit(name: object) -> IntentError:
    return IntentError(f"unknown unit {name!r}; the units are {', '.join(UNITS)}")


def unit(name: str) -> tuple[str, float, float]:
    try:
        return UNITS[name]
    except KeyError:
        raise _unknown_unit(name) from None


def convert(value: float, from_units: str, to_units: str) -> float:
    """`value` in `from_units` expressed in `to_units`; IntentError across dimensions."""
    source, target = unit(from_units), unit(to_units)
    if source[0] != target[0]:
        raise IntentError(f"cannot convert {from_units} ({source[0]}) to {to_units} ({target[0]})")
    return (value * source[1] + source[2] - target[2]) / target[1]


@dataclass(frozen=True)
class Quantity:
    """A value or a closed interval in one unit."""

    units: str
    value: float | None = None
    lower: float | None = None
    upper: float | None = None

    @property
    def dimension(self) -> str:
        return unit(self.units)[0]

    @property
    def bounds(self) -> tuple[float, float]:
        if self.value is not None:
            return (self.value, self.value)
        assert self.lower is not None and self.upper is not None
        return (self.lower, self.upper)

    def to(self, units: str) -> Quantity:
        if self.value is not None:
            return Quantity(units, value=convert(self.value, self.units, units))
        lower, upper = self.bounds
        return Quantity(units, lower=convert(lower, self.units, units), upper=convert(upper, self.units, units))

    def contains(self, other: Quantity) -> bool:
        """Whether `other`'s bounds lie inside these bounds (after conversion); False across dimensions."""
        if self.dimension != other.dimension:
            return False
        lower, upper = self.bounds
        other_lower, other_upper = other.to(self.units).bounds
        tolerance = 1e-9 * max(1.0, abs(lower), abs(upper))
        return lower - tolerance <= other_lower and other_upper <= upper + tolerance

    def as_dict(self) -> dict[str, Any]:
        if self.value is not None:
            return {"value": self.value, "units": self.units}
        return {"lower": self.lower, "upper": self.upper, "units": self.units}

    def __str__(self) -> str:
        if self.value is not None:
            return f"{self.value:g} {self.units}"
        return f"{self.lower:g}..{self.upper:g} {self.units}"


def _is_finite_number(value: object) -> bool:
    """Whether `value` is an int or float that a float holds and that is finite; an integer past float range is not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def quantity(data: Mapping[str, Any]) -> Quantity:
    """A Quantity from its schema form; IntentError for an unknown unit, non-finite number or reversed interval."""
    units = data.get("units")
    if not isinstance(units, str):
        raise _unknown_unit(units)
    unit(units)
    numbers = [data.get(key) for key in ("value", "lower", "upper") if key in data]
    if not numbers or not all(_is_finite_number(n) for n in numbers):
        raise IntentError(f"a quantity needs finite numbers, got {dict(data)!r}")
    if "value" in data:
        return Quantity(units, value=float(data["value"]))
    if "lower" not in data or "upper" not in data:
        given = "lower" if "lower" in data else "upper"
        raise IntentError(f"an interval needs both a lower and an upper bound, got only its {given} bound")
    lower, upper = float(data["lower"]), float(data["upper"])
    if lower > upper:
        raise IntentError(f"interval {lower:g}..{upper:g} {units} is reversed")
    return Quantity(units, lower=lower, upper=upper)
