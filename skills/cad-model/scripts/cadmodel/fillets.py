# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Opt-in per-edge fillet fallback for authored CAD model scripts."""

from __future__ import annotations

import logging
import math
from typing import Any


_LOG = logging.getLogger("cadmodel.fillets")


def _is_not_done(error: BaseException) -> bool:
    """Recognize OCP's StdFail_NotDone without importing OCP for non-kernel callers."""
    if type(error).__name__ == "StdFail_NotDone":
        return True
    try:
        from OCP.StdFail import StdFail_NotDone
    except ImportError:
        return False
    return isinstance(error, StdFail_NotDone)


def fillet_edge(shape: Any, edge: Any, radius: float, *, functional: bool = False,
                min_radius: float | None = None, retries: int = 2, name: str = "edge") -> Any:
    """Fillet one edge, halving the radius after ``StdFail_NotDone``.

    ``retries`` counts attempts after the requested radius, so the default tries radius, radius/2, and radius/4.
    ``min_radius`` (0 < min_radius <= radius) declares the smallest radius the design permits: no attempt goes below
    it. A functional edge without ``min_radius`` is tried at its specified radius only, so a refusal fails the build
    rather than weakening the part. A round made smaller than requested logs a warning with the actual radius. A
    non-functional edge is left sharp if every attempt fails; a functional edge re-raises the last kernel exception.
    """
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("fillet radius must be finite and positive")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("fillet retries must be a non-negative integer")
    if min_radius is not None and not (math.isfinite(min_radius) and 0 < min_radius <= radius):
        raise ValueError("fillet min_radius must be finite, positive and no larger than the radius")

    floor = min_radius if min_radius is not None else (radius if functional else 0.0)
    candidates = [r for r in (radius / (2 ** attempt) for attempt in range(retries + 1)) if r >= floor - 1e-12]
    last_error: Exception | None = None
    for candidate_radius in candidates:
        try:
            result = shape.fillet(candidate_radius, [edge])
        except Exception as error:
            if not _is_not_done(error):
                raise
            last_error = error
            continue
        if candidate_radius != radius:
            _LOG.warning("%s filleted at %.6g mm, not the requested %.6g mm, after the kernel refused larger radii",
                         name, candidate_radius, radius)
        return result

    assert last_error is not None
    if functional:
        raise last_error
    _LOG.warning("skipped non-functional fillet on %s after %d attempts (requested radius %.6g mm)",
                 name, len(candidates), radius)
    return shape
