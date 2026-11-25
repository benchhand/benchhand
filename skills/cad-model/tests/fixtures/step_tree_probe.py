# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""The product tree of assembly STEP files, read with cad-motion pose.read_assembly_step.

Usage: uv run --script step_tree_probe.py STEP...
stdout: {path: {component name: [product path from the root]}}.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from cadmodel.motion import load_pose  # noqa: E402

pose = load_pose()
json.dump({path: {c.name: list(c.path) for c in pose.read_assembly_step(Path(path)).components} for path in sys.argv[1:]},
          sys.stdout)
