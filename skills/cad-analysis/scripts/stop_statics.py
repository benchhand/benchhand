"""Rigid statics of a hard stop, without a geometry kernel.

A lever pushed against a stop turns about its pivot. The applied moment M is the peak force times the
distance to where it acts; the stop answers with a reaction on its contact patch. Each point of the patch
has an arm, its signed lever about the pivot axis. A rigid stop does not fix how the reaction spreads over
the patch, so statics bounds it:

- the design reaction is M over the smallest arm, the whole reaction on the nearest edge;
- the lower bound is M over the largest arm;
- uniform pressure gives M over the mean arm, informational;
- a compliant stop, whose pressure grows with the arm, gives M * (integral of arm) / (integral of arm squared),
  informational.

A smallest arm of zero or less leaves the design reaction unbounded: any load, however small, balances
about an edge on the axis. The pivot then carries the reaction plus the applied force.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BOUNDED = "bounded"
UNBOUNDED = "unbounded"
UNDERSTATED = "understated"
RECORDED_TOLERANCE = 1e-9  # relative: a recorded figure equal to the design reaction passes
STOP_ARM_EPSILON_MM = 1e-6  # float noise: an arm this small is on the axis, so rounding to either side of zero changes no verdict


@dataclass(frozen=True)
class Patch:
    """A contact patch seen from the pivot axis: the smallest and largest arm (mm) and the integrals of
    the arm over its area (mm^2): area, integral of arm, integral of arm squared."""

    min_arm: float
    max_arm: float
    area: float
    first: float
    second: float

    @classmethod
    def strip(cls, lo: float, hi: float, width: float = 1.0) -> Patch:
        """A rectangle whose arm runs linearly from `lo` to `hi` across its length and is constant across `width`."""
        return cls(lo, hi, (hi - lo) * width, width * (hi**2 - lo**2) / 2.0, width * (hi**3 - lo**3) / 3.0)


@dataclass(frozen=True)
class Reaction:
    """The statics of one stop. `outcome` is the check outcome (pass, fail or unknown), `status` the reason."""

    status: str
    outcome: str
    severity: str
    moment_n_mm: float
    design_n: float | None
    rigid_lower_n: float | None
    uniform_n: float | None
    compliant_n: float | None
    pin_load_n: float | None
    message: str


def reaction(force_n: float, load_arm_mm: float, patch: Patch, recorded_n: float | None = None) -> Reaction:
    """The reaction of a stop against `force_n` applied `load_arm_mm` from the axis, over `patch`, judged
    against the recorded contact load `recorded_n` when there is one."""
    if not (math.isfinite(force_n) and force_n > 0 and math.isfinite(load_arm_mm) and load_arm_mm > 0):
        raise ValueError(f"force {force_n} N and load arm {load_arm_mm} mm must be positive and finite")
    moment = force_n * load_arm_mm
    uniform = moment * patch.area / patch.first if patch.first > 0 else None
    compliant = moment * patch.first / patch.second if patch.first > 0 and patch.second > 0 else None
    lower = moment / patch.max_arm if patch.max_arm > 0 else None

    def result(status: str, outcome: str, severity: str, design: float | None, message: str) -> Reaction:
        pin_load = design + force_n if design is not None else None
        return Reaction(status, outcome, severity, moment, design, lower, uniform, compliant, pin_load, message)

    if patch.min_arm <= STOP_ARM_EPSILON_MM:
        return result(UNBOUNDED, "unknown", "critical", None,
                      f"the contact reaches the pivot axis (smallest arm {patch.min_arm:.4g} mm), so a rigid stop has no "
                      "bounded reaction: model the contact in an FEA contact case and approve it in a design record")
    design = moment / patch.min_arm
    if recorded_n is not None and recorded_n < design * (1.0 - RECORDED_TOLERANCE):
        return result(UNDERSTATED, "fail", "major", design,
                      f"the recorded contact load {recorded_n:.4g} N is below the design reaction {design:.4g} N "
                      f"(M {moment:.4g} N*mm over the smallest arm {patch.min_arm:.4g} mm)")
    return result(BOUNDED, "pass", "info", design,
                  f"design reaction {design:.4g} N (M {moment:.4g} N*mm over the smallest arm {patch.min_arm:.4g} mm); "
                  f"the pivot carries {design + force_n:.4g} N")
