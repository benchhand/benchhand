"""Where a path may touch a fitted partner by design: the engagement of an interference-type fit.

A press fit or heat-set insert touches its partner over the last stretch of the insertion path.
`classify` sorts the shared-volume samples of one leg against one such partner. The engagement is
the unbroken run of interfering samples that starts at the installed pose and carries on across waypoints.
The declared engagement_length_mm caps it. Interference outside the engagement is a collision. A run
with no declared length stays unknown and never counts as exempt. Pure arithmetic: access.py does the geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Engagement:
    """The verdict on the samples of one leg against one fitted partner.

    `exempt` are the positions inside the engagement and `collisions` the interfering positions outside it.
    `needs` are positions to sample before the verdict is final. While it holds any, the other fields are
    provisional. `extent` is the far end of the run, `reached_start` says the run covers the whole leg, and
    `unknown` says the run is real but has no declared length."""

    exempt: frozenset[float]
    collisions: list[float]
    needs: list[float]
    extent: float
    reached_start: bool
    unknown: bool


def classify(samples: Mapping[float, float | None], *, tail_mm: float, engaged: bool, cap_mm: float | None,
             min_step_mm: float, threshold: float) -> Engagement:
    """Classify `samples`, which map the distance s back from the leg's end (mm) to the shared volume (mm3), or None.

    `tail_mm` is the path length from the leg's end to the installed pose, `engaged` says the run reached the
    leg's end, `cap_mm` is the declared engagement length (None when absent). A sample past the cap by more
    than `min_step_mm` collides, the resolution of the run's end."""
    positions = sorted(samples)

    def hit(s: float) -> bool:
        value = samples[s]
        return value is not None and value > threshold

    limit = None if cap_mm is None else cap_mm - tail_mm + min_step_mm  # farthest s inside the cap
    run: list[float] = []
    needs: list[float] = []
    reached = False
    if engaged and positions and hit(positions[0]):
        run.append(positions[0])
        reached = True
        for a, b in zip(positions, positions[1:]):
            if limit is not None and hit(b) and b > limit:
                needs = []  # the run is longer than the cap whatever lies between: a collision
                reached = False
                break
            if limit is not None and a <= limit and b - a > min_step_mm and samples[b] is not None:
                needs.append((a + b) / 2)
            if not hit(b):
                reached = False
                break
            run.append(b)
    exempt = frozenset(run)
    collisions = [s for s in positions if hit(s) and s not in exempt]
    return Engagement(exempt, collisions, needs, run[-1] if run else 0.0,
                      reached and not needs and not collisions, bool(run) and cap_mm is None)
