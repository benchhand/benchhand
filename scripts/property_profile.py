"""The one Hypothesis profile of the property tests.

Tests put this directory on sys.path (see modload.py) and import this module before they use @given.
derandomize=True replays the same examples on every run, locally and in CI, so a failure reproduces
without a seed and a green run cannot turn red on its own. The example database stays off so no
state leaks between runs; a shrunk failure goes into the test as an @example.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

PROFILE = "cad-agent"

settings.register_profile(
    PROFILE,
    max_examples=200,
    derandomize=True,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(PROFILE)
