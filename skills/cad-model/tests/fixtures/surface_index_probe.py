# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy==2.5.5", "hypothesis==6.170.15"]
# ///

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Properties of `cadmodel.agreement._Surface`, the triangle lookup of the agreement check, on meshes built here.

The lookup must answer each point exactly as a search of every triangle does: a triangle counts when its box,
grown by the reach, holds the point and the point lies within the reach of it. test_agreement.py runs each test
here in its own process. Usage: uv run --script surface_index_probe.py [SurfaceIndex.test_name ...]
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from hypothesis import given, strategies as st

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
sys.path.insert(0, str(HERE.parents[4] / "scripts"))
from cadmodel import agreement  # noqa: E402
import property_profile  # noqa: E402,F401  (registers and loads the profile)

LATTICE = st.integers(-400, 400).map(lambda k: k / 8)  # mm, exact in binary, so points land on cell borders
POINT = st.tuples(LATTICE, LATTICE, LATTICE)
REACH = st.sampled_from([0.125, 0.155, 0.25, 0.5, 2.0])
NEAR_REACH = st.sampled_from([0.0, 0.5, 0.999999, 1.0, 1.000001, 1.5])  # offsets in reaches from a triangle


@st.composite
def triangle(draw: st.DrawFn) -> np.ndarray:
    """Corners (3, 3): a small triangle, a long thin sliver, or three free corners."""
    a = np.array(draw(POINT))
    kind = draw(st.sampled_from(["small", "sliver", "free"]))
    small = st.tuples(*[st.integers(-16, 16).map(lambda k: k / 8)] * 3)
    if kind == "small":
        b, c = a + np.array(draw(small)), a + np.array(draw(small))
    elif kind == "sliver":
        b = np.array(draw(POINT))
        tiny = np.array(draw(st.tuples(*[st.integers(-2, 2).map(lambda k: k / 64)] * 3)))
        c = a + (b - a) * draw(st.integers(0, 8)) / 8 + tiny
    else:
        b, c = np.array(draw(POINT)), np.array(draw(POINT))
    return np.stack([a, b, c])


@st.composite
def case(draw: st.DrawFn) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """(corners (M, 3, 3), reach, free points (K, 3), cell-border picks (J, 4)) for one lookup."""
    corners = np.stack(draw(st.lists(triangle(), min_size=1, max_size=40)))
    reach = draw(REACH)
    points = [np.array(p) for p in draw(st.lists(POINT, max_size=20))]
    for _ in range(draw(st.integers(0, 30))):  # points at a set fraction of the reach from a triangle
        corner = corners[draw(st.integers(0, len(corners) - 1))]
        weights = np.array(draw(st.tuples(*[st.integers(0, 4)] * 3)), dtype=float) + 1e-3
        direction = np.array(draw(st.tuples(*[st.integers(-3, 3)] * 3)), dtype=float) + (1e-3, 0.0, 0.0)
        offset = direction / np.linalg.norm(direction) * reach * draw(NEAR_REACH)
        points.append(weights @ corner / weights.sum() + offset)
    borders = np.array(draw(st.lists(st.tuples(st.integers(-2, 70), st.integers(-2, 70), st.integers(-2, 70),
                                               st.integers(0, 7)), max_size=20)), dtype=float).reshape(-1, 4)
    return corners, reach, np.array(points).reshape(-1, 3), borders


def mesh_of(corners: np.ndarray) -> agreement.Mesh:
    return agreement.Mesh(corners.reshape(-1, 3), np.arange(3 * len(corners)).reshape(-1, 3))


def brute_force(corners: np.ndarray, reach: float, points: np.ndarray) -> np.ndarray:
    """Per point: whether a triangle within reach of it exists, from every point and triangle pair."""
    pairs_p = np.repeat(points, len(corners), axis=0)
    pairs_t = np.tile(np.arange(len(corners)), len(points))
    low, high = corners.min(axis=1) - reach, corners.max(axis=1) + reach
    boxed = np.all((pairs_p >= low[pairs_t]) & (pairs_p <= high[pairs_t]), axis=1)
    picked = corners[pairs_t]
    squared = agreement._triangle_sq(pairs_p, picked[:, 0], picked[:, 1], picked[:, 2])
    return (boxed & (squared <= reach * reach)).reshape(len(points), len(corners)).any(axis=1)


def square(size: float, steps: int) -> agreement.Mesh:
    """A size x size square in z = 0 centered on the origin, in steps x steps cells of two triangles."""
    ticks = np.linspace(-size / 2, size / 2, steps + 1)
    x, y = np.meshgrid(ticks, ticks, indexing="ij")
    points = np.stack([x.ravel(), y.ravel(), np.zeros(x.size)], axis=1)
    corner = (np.arange(steps)[:, None] * (steps + 1) + np.arange(steps)[None, :]).ravel()
    triangles = np.concatenate([np.stack([corner, corner + steps + 1, corner + steps + 2], axis=1),
                                np.stack([corner, corner + steps + 2, corner + 1], axis=1)])
    return agreement.Mesh(points, triangles)


def cylinder(radius: float, height: float, segments: int) -> agreement.Mesh:
    """The side wall of a cylinder standing on z = 0 at the origin: tall thin triangles."""
    angles = np.linspace(0.0, 2 * np.pi, segments, endpoint=False)
    ring = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(segments)], axis=1)
    points = np.concatenate([ring, ring + (0.0, 0.0, height)])
    here, there = np.arange(segments), (np.arange(segments) + 1) % segments
    triangles = np.concatenate([np.stack([here, there, there + segments], axis=1),
                                np.stack([here, there + segments, here + segments], axis=1)])
    return agreement.Mesh(points, triangles)


def joined(*meshes: agreement.Mesh) -> agreement.Mesh:
    offsets = np.cumsum([0] + [len(m.points) for m in meshes[:-1]])
    return agreement.Mesh(np.concatenate([m.points for m in meshes]),
                          np.concatenate([m.triangles + offset for m, offset in zip(meshes, offsets)]))


class SurfaceIndex(unittest.TestCase):
    @given(case())
    def test_the_lookup_answers_every_point_as_a_search_of_every_triangle(
            self, drawn: tuple[np.ndarray, float, np.ndarray, np.ndarray]) -> None:
        corners, reach, points, borders = drawn
        surface = agreement._Surface(mesh_of(corners), reach)
        # Border picks: a cell corner of the lookup's own grid on the axes the mask names, else a free coordinate
        mask = ((borders[:, 3:4].astype(int) >> np.arange(3)) & 1).astype(bool)
        on_border = surface.origin + borders[:, :3] * surface.cell
        free = corners.reshape(-1, 3)[np.arange(len(borders)) % (3 * len(corners))]
        points = np.concatenate([points, np.where(mask, on_border, free)])
        if not len(points):
            return
        np.testing.assert_array_equal(surface.within(points), brute_force(corners, reach, points))

    def test_a_large_face_of_two_triangles_agrees_with_a_fine_mesh_of_it(self) -> None:
        """Most triangles are a small cylinder's thin walls, so the face's two triangles each cover thousands of
        lookup cells. Every sample of the fine face still finds them."""
        wall = cylinder(3.0, 3.0, 200)
        coarse, fine = joined(square(200.0, 1), wall), joined(square(200.0, 40), wall)
        self.assertEqual(agreement.compare(coarse, fine, 0.05)["status"], "pass")
        self.assertEqual(agreement.compare(fine, coarse, 0.05)["status"], "pass")

    def test_a_face_off_axis_moved_0_2_mm_along_its_normal_does_not_agree(self) -> None:
        """0.2 mm is beyond the 0.155 mm reach at 0.05 mm. The face is tilted off every axis, so the grown box of
        each of its triangles still holds the moved points: only the distance to the triangle finds them off."""
        rng = np.random.default_rng(20261120)
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        normal = rotation[:, 2]
        self.assertLess(float(np.abs(normal).max()), 0.9, normal)  # the seed gives an off-axis face
        face = square(40.0, 10)
        drawn = agreement.Mesh(face.points @ rotation.T, face.triangles)
        depicted = agreement.Mesh(drawn.points + 0.2 * normal, face.triangles)
        self.assertEqual(agreement.compare(drawn, depicted, 0.05)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
