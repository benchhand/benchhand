#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["click==8.5.1", "mypy==2.4.1"]
# ///
"""Run mypy over changed feature modules and strict-check new pure modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "mypy.ini"
CHANGED_FILES = (
    "skills/cad-analysis/scripts/fea_conditions.py",
    "skills/cad-analysis/scripts/fea_part_selection.py",
    "skills/cad-analysis/scripts/fea_reaction.py",
    "skills/cad-analysis/scripts/fea_static.py",
    "skills/cad-analysis/scripts/fea_stress.py",
    "skills/cad-analysis/scripts/fea_stress_study.py",
    "skills/cad-analysis/scripts/mesh_quality.py",
    "skills/cad-analysis/scripts/stop_extrema.py",
    "skills/cad-analysis/scripts/stop_reaction.py",
    "skills/cad-analysis/scripts/stop_statics.py",
    "skills/cad-design/scripts/form_check.py",
    "skills/cad-design/scripts/process_checks/__init__.py",
    "skills/cad-design/scripts/process_checks/additive.py",
    "skills/cad-design/scripts/process_checks/core.py",
    "skills/cad-design/scripts/process_checks/dispatch.py",
    "skills/cad-design/scripts/process_checks/machining.py",
    "skills/cad-design/scripts/process_checks/purchased.py",
    "skills/cad-design/scripts/process_checks/route.py",
    "skills/cad-design/scripts/process_checks/secondary.py",
    "skills/cad-design/scripts/process_checks/shaping.py",
    "skills/cad-design/scripts/process_checks/sheet.py",
    "skills/cad-drawings/scripts/assembly_check.py",
    "skills/cad-drawings/scripts/assembly_drawing.py",
    "skills/cad-drawings/scripts/assembly_sheet.py",
    "skills/cad-record/scripts/check_packet.py",
    "skills/cad-record/scripts/cli.py",
    "skills/cad-fits/scripts/stackup.py",
    "skills/cad-flow/scripts/fea_solvers.py",
    "skills/cad-flow/scripts/scratch.py",
    "skills/cad-model/scripts/cad_model.py",
    "skills/cad-model/scripts/cadmodel/build.py",
    "skills/cad-model/scripts/cadmodel/check.py",
    "skills/cad-model/scripts/cadmodel/components.py",
    "skills/cad-model/scripts/cadmodel/diff.py",
    "skills/cad-model/scripts/cadmodel/manufacture.py",
    "skills/cad-model/scripts/cadmodel/occ.py",
    "skills/cad-model/scripts/cadmodel/orientation.py",
    "skills/cad-model/scripts/cadmodel/record.py",
    "skills/cad-model/scripts/cadmodel/runner.py",
    "skills/cad-model/scripts/cadmodel/store.py",
    "skills/cad-motion/scripts/access.py",
    "skills/cad-motion/scripts/incremental.py",
    "skills/cad-motion/scripts/pair_policy.py",
    "skills/cad-motion/scripts/play.py",
    "skills/cad-motion/scripts/sweep.py",
    "skills/cad-recheck/scripts/stamp.py",
    "skills/cad-record/scripts/assembly_operations.py",
    "skills/cad-record/scripts/build_guide.py",
    "skills/cad-record/scripts/design_rules.py",
    "skills/cad-record/scripts/evidence.py",
    "skills/cad-record/scripts/evidence_paths.py",
    "skills/cad-record/scripts/fail_fast.py",
    "skills/cad-record/scripts/impact.py",
    "skills/cad-record/scripts/layout.py",
    "skills/cad-record/scripts/manufacturing_intent.py",
    "skills/cad-record/scripts/prune.py",
    "skills/cad-record/scripts/release.py",
    "skills/cad-record/scripts/schema_registry.py",
    "skills/cad-record/scripts/selection.py",
    "skills/cad-record/scripts/store.py",
    "skills/cad-record/scripts/validate.py",
    "skills/cad-record/scripts/yaml_loader.py",
    "skills/cad-render/scripts/render.py",
    "skills/cad-design/scripts/build_reference.py",
    "skills/cad-fits/scripts/fits.py",
    "skills/cad-model/scripts/cadmodel/__init__.py",
    "skills/cad-model/scripts/cadmodel/fillets.py",
    "skills/cad-model/scripts/cadmodel/holes.py",
    "skills/cad-model/scripts/cadmodel/mesh.py",
    "skills/cad-model/scripts/cadmodel/motion.py",
    "skills/cad-model/scripts/cadmodel/provenance.py",
    "skills/cad-model/scripts/cadmodel/safe.py",
    "skills/cad-model/scripts/cadmodel/scriptenv.py",
    "skills/cad-model/scripts/cadmodel/stepfile.py",
    "skills/cad-model/scripts/run_build123d.py",
    "skills/cad-model/scripts/run_cadquery.py",
    "skills/cad-motion/scripts/continuous.py",
    "skills/cad-motion/scripts/plan.py",
    "skills/cad-motion/scripts/spot_check.py",
    "skills/cad-motion/scripts/subsystems.py",
    "skills/cad-render/scripts/compare_renders.py",
    "skills/cad-render/scripts/materials.py",
    "skills/cad-render/scripts/parity_policy.py",
    "skills/cad-viewer/scripts/cadviewer/__init__.py",
    "skills/cad-viewer/scripts/viewer.py",
    "scripts/typecheck.py",
    "skills/cad-design/tests/mfg_corpus/capability_profile.py",
    "skills/cad-design/tests/mfg_corpus/corpus.py",
    "skills/cad-design/tests/mfg_corpus/doc_support.py",
    "skills/cad-design/tests/mfg_corpus/rates.py",
    "skills/cad-design/tests/mfg_corpus/support.py",
    "skills/cad-design/tests/mfg_corpus/families/access_assembly.py",
    "skills/cad-design/tests/mfg_corpus/families/additive_min_feature.py",
    "skills/cad-design/tests/mfg_corpus/families/additive_stability.py",
    "skills/cad-design/tests/mfg_corpus/families/additive_support_overhang_bridge.py",
    "skills/cad-design/tests/mfg_corpus/families/assembly_operations_order.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_fastener_bom.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_heatset_insert.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_install_step.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_instruction_order.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_manufacturing.py",
    "skills/cad-design/tests/mfg_corpus/families/doc_retaining_ring.py",
    "skills/cad-design/tests/mfg_corpus/families/fdm_horizontal_hole.py",
    "skills/cad-design/tests/mfg_corpus/families/form_check_kernel_validity.py",
)
STRICT_FILES = (
    "skills/cad-analysis/scripts/fea_conditions.py",
    "skills/cad-analysis/scripts/fea_part_selection.py",
    "skills/cad-analysis/scripts/fea_reaction.py",
    "skills/cad-analysis/scripts/fea_stress.py",
    "skills/cad-analysis/scripts/fea_stress_study.py",
    "skills/cad-analysis/scripts/mesh_quality.py",
    "skills/cad-analysis/scripts/stop_statics.py",
    "skills/cad-flow/scripts/fea_solvers.py",
    "skills/cad-design/scripts/process_checks/__init__.py",
    "skills/cad-design/scripts/process_checks/additive.py",
    "skills/cad-design/scripts/process_checks/core.py",
    "skills/cad-design/scripts/process_checks/dispatch.py",
    "skills/cad-design/scripts/process_checks/machining.py",
    "skills/cad-design/scripts/process_checks/purchased.py",
    "skills/cad-design/scripts/process_checks/route.py",
    "skills/cad-design/scripts/process_checks/secondary.py",
    "skills/cad-design/scripts/process_checks/shaping.py",
    "skills/cad-design/scripts/process_checks/sheet.py",
    "skills/cad-model/scripts/cadmodel/components.py",
    "skills/cad-model/scripts/cadmodel/threemf.py",
    "skills/cad-record/scripts/assembly_operations.py",
    "skills/cad-record/scripts/evidence.py",
    "skills/cad-record/scripts/evidence_paths.py",
    "skills/cad-record/scripts/fail_fast.py",
    "skills/cad-record/scripts/impact.py",
    "skills/cad-record/scripts/selection.py",
    "skills/cad-record/scripts/check_packet.py",
    "skills/cad-record/scripts/cli.py",
    "skills/cad-record/scripts/schema_registry.py",
    "skills/cad-record/scripts/yaml_loader.py",
)


def main() -> int:
    print("Checking all changed/new production modules with configured incremental baseline.")
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(CONFIG), *CHANGED_FILES],
        cwd=ROOT,
        check=False,
    )
    print("Strictly checking new pure feature modules.")
    strict = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(CONFIG), "--strict", *STRICT_FILES],
        cwd=ROOT,
        check=False,
    )
    return 1 if result.returncode or strict.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
