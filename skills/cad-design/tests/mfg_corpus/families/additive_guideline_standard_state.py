# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""additive.geometry on a printer that records calibration_state standard and no calibration: the shipped guideline
values (smallest pin and hole 2 mm, minimum wall 1.2 mm on a 0.4 mm nozzle) decide, exactly as without the state. The state is wording and never moves a verdict.

Judged on the fdm geometry obligation. Millimetres, bed frame, +Z up.
"""

from typing import Any

from corpus import Family, Outcome, Pair
from support import printed_verdict, solids

GEOMETRY = "process.fdm.geometry@"


def _verdict(shape: Any) -> Outcome:
    return printed_verdict(shape, GEOMETRY, standard_state=True)


def thin_plate(mutant: bool) -> Outcome:
    """A 0.8 mm plate against the 1.2 mm guideline minimum wall of a 0.4 mm nozzle."""
    s = solids()
    return _verdict(s.box(0, 0, 0, 30, 20, 0.8 if mutant else 2.0))


def thin_pin(mutant: bool) -> Outcome:
    s = solids()
    return _verdict(s.fuse(s.box(0, 0, 0, 20, 20, 3), s.cylinder(10, 10, 3, 0.75 if mutant else 2.0, 10)))


def tiny_hole(mutant: bool) -> Outcome:
    """A vertical through hole of 1 mm against 4 mm in a 10 mm block."""
    s = solids()
    return _verdict(s.cut(s.box(0, 0, 0, 20, 20, 10), s.cylinder(10, 10, -1, 0.5 if mutant else 2.0, 12)))


FAMILY = Family(
    id="additive_guideline_standard_state", check="process_checks/additive.py geometry (small_feature_check, guideline limits)",
    owner="cad-design",
    pairs=(
        Pair("thin_plate", "0.8 mm plate against the 1.2 mm guideline minimum wall, standard state recorded", thin_plate),
        Pair("thin_pin", "1.5 mm pin against the 2 mm guideline minimum feature, standard state recorded", thin_pin),
        Pair("tiny_hole", "1 mm hole against the 2 mm guideline minimum hole, standard state recorded", tiny_hole),
    ))
