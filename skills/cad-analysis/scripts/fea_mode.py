"""FreeCAD-independent FEA mesh mode: which mode a flow profile asks for, and when a fast result is solved again.

Two modes mesh the same case:

- ``accurate``: curved second-order tetrahedra, with Gmsh's elastic and optimising high-order passes.
- ``fast``: straight-sided second-order tetrahedra (Mesh.SecondOrderLinear 1, Mesh.HighOrderOptimize 0). They are
  valid whenever the linear mesh is, skip the high-order passes, and miss the curvature of curved faces. A fast
  result carries that measured bias as its stated uncertainty.

A fast result whose margin to an acceptance criterion lies within the band is solved again in accurate mode; a
clear pass or a clear fail stays fast. The mode and band come from the flow profile's FEA setting in
product.yaml (cad-flow flow_profile.py owns the schema); FLOW_FEA names its fields in one place.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# product.yaml flow.profile.<setting>: a scalar on/off, or a mapping {enabled, mode, fast_band_pct}.
FLOW_FEA = {"setting": "fea", "enabled": "enabled", "mode": "mode", "band": "fast_band_pct"}
MODES = ("fast", "accurate")
DEFAULT_MODE = "accurate"
MAX_BAND_PCT = 50.0
# The worst unconservative signed error of a fast result against a converged curved reference, in percent of the
# reference (negative: fast under-reports). Measured on dev against curved level 2 at 3 mm: LMP-0002 at 6, 4 and
# 3 mm gives max von Mises -9.73, +10.46 and -1.40 % and max displacement -1.43, -1.60 and -1.83 %; the
# cantilever at 5 and 3 mm gives 0 % for both (PR body: fast-mode bias table).
FAST_BIAS_PCT = {"max_von_mises": -9.73, "max_displacement": -1.83}
DEFAULT_BAND_PCT = 19.5  # twice the larger magnitude in FAST_BIAS_PCT, rounded up to 0.5 %
_SWITCH = {True: True, False: False, "on": True, "off": False, "not_asked": True}


class ModeError(ValueError):
    """The flow profile's FEA setting names no mode this producer can run."""


@dataclass(frozen=True)
class Mode:
    mode: str  # fast or accurate
    band_pct: float | None  # the profile's band for a fast result, or None for the measured default


@dataclass(frozen=True)
class Check:
    """One acceptance criterion of a solved case: `value` must satisfy `operator` (>= or <=) `limit`."""

    name: str
    value: float
    operator: str
    limit: float


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _switch(value: Any) -> bool:
    """A YAML on/off switch: a boolean (bare on/off parses as one) or the strings on, off and not_asked."""
    return isinstance(value, bool) or (isinstance(value, str) and value in _SWITCH)


def read_mode(product: Mapping[str, Any]) -> Mode:
    """The mode product.yaml's flow profile asks for; an absent, scalar or partial setting is accurate."""
    setting = _mapping(_mapping(_mapping(product).get("flow")).get("profile")).get(FLOW_FEA["setting"], "not_asked")
    if not isinstance(setting, Mapping):
        if _switch(setting):
            return Mode(DEFAULT_MODE, None)
        raise ModeError(f"flow.profile.{FLOW_FEA['setting']} must be on, off or a mapping, not {setting!r}")
    enabled = setting.get(FLOW_FEA["enabled"], True)
    if not _switch(enabled):
        raise ModeError(f"flow.profile.{FLOW_FEA['setting']}.{FLOW_FEA['enabled']} must be on or off, not {enabled!r}")
    mode = setting.get(FLOW_FEA["mode"], DEFAULT_MODE)
    if isinstance(mode, bool) or mode not in MODES:
        raise ModeError(f"flow.profile.{FLOW_FEA['setting']}.{FLOW_FEA['mode']} must be one of {', '.join(MODES)}, "
                        f"not {mode!r} (bare YAML on/off parses as a boolean)")
    band = setting.get(FLOW_FEA["band"])
    if band is None:
        return Mode(mode, None)
    if mode != "fast":
        raise ModeError(f"flow.profile.{FLOW_FEA['setting']}.{FLOW_FEA['band']} applies only to fast mode")
    if isinstance(band, bool) or not isinstance(band, (int, float)) or not 0 < band <= MAX_BAND_PCT:
        raise ModeError(f"flow.profile.{FLOW_FEA['setting']}.{FLOW_FEA['band']} must be a number in (0, "
                        f"{MAX_BAND_PCT:g}], not {band!r}")
    return Mode(mode, float(band))


def escalation(checks: Sequence[Check], band_pct: float) -> tuple[bool, str]:
    """Whether a fast result must be solved again in accurate mode, and why: some criterion's value lies within
    `band_pct` percent of its limit, on either side."""
    # A margin exactly at the band escalates; the factor absorbs the rounding of value - limit.
    near = [c for c in checks if abs(c.value - c.limit) <= band_pct / 100.0 * abs(c.limit) * (1 + 1e-9)]
    if near:
        return True, "; ".join(f"{c.name} {c.value:.6g} is within {band_pct:g}% of its limit {c.operator} {c.limit:.6g}"
                               for c in near)
    if not checks:
        return False, "no acceptance criterion: the fast result decides no verdict"
    return False, (f"every criterion is clear of its limit by more than {band_pct:g}%: "
                   + "; ".join(f"{c.name} {c.value:.6g} {c.operator} {c.limit:.6g}" for c in checks))
