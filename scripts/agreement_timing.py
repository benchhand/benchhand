#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy==2.5.5"]
# ///

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Times `cadmodel.agreement.compare` on a fixed, generated mesh pair, so a timing needs no FreeCAD and no export.

The pair is a large curved part: a torus (major radius 45 mm, minor radius 12 mm, close to the 44.85 mm fillet torus
of lamp-arm's LMP-0004, grown to a size that loads the lookup). The "GLB" mesh is 96 x 48 quads (9216 triangles). The
"STEP reference" is the same torus at 128 x 64 quads (16384 triangles), moved by a small seeded shift of its grid
phase and seeded noise of up to 0.002 mm, as a second tessellation of one surface differs from the first. Both meshes
are rounded to 0.1 micrometer, so the digest holds across platforms. The chord error of the coarse mesh is below
0.031 mm, inside the 0.05 mm deflection that `compare` takes, so the verdict is `pass`.

Usage: uv run --script scripts/agreement_timing.py [--runs N] [--json]
Run it on one host before and after a change to the agreement code. Timings differ between hosts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "cad-model" / "scripts"))
from cadmodel import agreement  # noqa: E402

SEED = 20260429
MAJOR_MM, MINOR_MM = 45.0, 12.0
GLB_GRID = (96, 48)    # quads around the ring, and around the tube
STEP_GRID = (128, 64)
NOISE_MM = 0.002
LINEAR_MM = 0.05       # the GLB deflection the exporter caps a large assembly at
UNIT_MM = 1e-4         # coordinates are rounded to this grid


def torus(grid: tuple[int, int], phase: tuple[float, float], noise: np.ndarray | None = None) -> agreement.Mesh:
    """A torus as a quad grid split into two triangles each, the seam shared by index."""
    nu, nv = grid
    u = (np.arange(nu) + phase[0]) * (2 * np.pi / nu)
    v = (np.arange(nv) + phase[1]) * (2 * np.pi / nv)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    ring = MAJOR_MM + MINOR_MM * np.cos(vv)
    points = np.stack([ring * np.cos(uu), ring * np.sin(uu), MINOR_MM * np.sin(vv)], axis=-1).reshape(-1, 3)
    if noise is not None:
        points = points + noise.uniform(-NOISE_MM, NOISE_MM, points.shape)
    points = np.round(points / UNIT_MM) * UNIT_MM
    index = np.arange(nu * nv).reshape(nu, nv)
    a, b = index, np.roll(index, -1, axis=0)
    c, d = np.roll(index, -1, axis=1), np.roll(b, -1, axis=1)
    triangles = np.concatenate([np.stack([a, b, d], axis=-1).reshape(-1, 3),
                                np.stack([a, d, c], axis=-1).reshape(-1, 3)])
    return agreement.Mesh(points, triangles.astype(np.int64))


def reference_pair(seed: int = SEED) -> tuple[agreement.Mesh, agreement.Mesh]:
    """(glb, step): the coarse and the fine tessellation of the torus, the same for one seed."""
    rng = np.random.default_rng(seed)
    phase = tuple(float(x) for x in rng.uniform(0.0, 0.5, 2))
    glb = torus(GLB_GRID, (0.0, 0.0), rng)
    step = torus(STEP_GRID, phase, rng)
    return glb, step


def digest(*meshes: agreement.Mesh) -> str:
    """SHA-256 over the points (as integer 0.1 um units) and the triangle indices of each mesh."""
    sha = hashlib.sha256()
    for mesh in meshes:
        sha.update(np.round(mesh.points / UNIT_MM).astype("<i8").tobytes())
        sha.update(mesh.triangles.astype("<i8").tobytes())
    return sha.hexdigest()


def measure(runs: int, seed: int = SEED) -> dict[str, Any]:
    glb, step = reference_pair(seed)
    seconds = []
    verdict: dict[str, Any] = {}
    for _ in range(runs):
        started = time.perf_counter()
        verdict = agreement.compare(glb, step, LINEAR_MM)
        seconds.append(time.perf_counter() - started)
    return {
        "seed": seed, "digest": digest(glb, step), "status": verdict["status"], "runs": runs,
        "glb_triangles": len(glb.triangles), "step_triangles": len(step.triangles),
        "glb_samples": len(agreement._samples(glb)), "step_samples": len(agreement._samples(step)),
        "median_s": statistics.median(seconds), "min_s": min(seconds), "max_s": max(seconds),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="timed compare calls (default 5)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    result = measure(args.runs)
    if args.json:
        print(json.dumps(result))
        return 0
    print(f"compare on the reference pair: status {result['status']}, seed {result['seed']}")
    print(f"triangles: GLB {result['glb_triangles']}, STEP {result['step_triangles']}; "
          f"samples: GLB {result['glb_samples']}, STEP {result['step_samples']}")
    print(f"{result['runs']} runs: median {result['median_s']:.3f} s, "
          f"range {result['min_s']:.3f} to {result['max_s']:.3f} s")
    print(f"digest {result['digest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
