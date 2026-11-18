---
name: cad-analysis
description: Use for static FreeCAD finite-element analysis of a hardware case, producing checked stress, displacement, and yield-safety results.
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD Analysis

TRIGGER
+ A hardware project is at L4 ANALYSIS and needs a static FEA case.
- Motion, transient, fatigue, buckling, thermal, or nonlinear analysis → report unsupported; this skill handles static linear analysis only.

## Workflow

1. LOAD `references/material-evidence.md`. Prepare a version-bound case with a checked STEP, intended part or occurrence, load/support model, mesh parameters and material conditions matched to the real part and requirement. Unknown or mismatched properties cannot support acceptance.
2. Store each authored case at `hw/analysis/cases/<case>/case.yaml`; include `step`, `fixed_faces`, non-empty `loads`, `mesh_size` and a record-bound `material` profile plus `conditions`. A face selector is exactly `{indices: [<1-based-face-index>, ...]}` or `{plane: {axis: x|y|z, value: <mm>, tolerance: <mm>}}`. Each load is `{selector: <face selector>, force: [Fx, Fy, Fz]}` in N, optionally with `load: <use-case-load-id>`.
3. Run from the project root:

   ```sh
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-analysis/scripts/fea_static.py hw/analysis/cases/<case>/case.yaml --hw hw --version vN [--case-id <case>] [--replace]
   ```

   The case YAML is positional. For a sweep worst pose, set `step` and `evidence` to the exact versioned `worst.step` and sibling `worst.step.json`; the solver verifies the STEP hash and full pose and never re-poses it. An intent case publishes analysis under `hw/analysis/vN/<case>/` and its engineering packet at `hw/checks/vN/fea-<case>/`. A standalone run's `--out` must be a new `.cad-work/` path.
4. Inspect warnings, solver versions and `worst_case_pose` (STEP, pose, evidence hash). Record accepted checks in `verification.csv` only with scoped evidence. Static simulation is never physical validation.

## Inputs

- `step`: STEP path, relative to the case YAML or absolute.
- `part`: optional PN-first identifier; required for ambiguous multi-solid STEP except when `occurrence` or `solid_index` selects one. Omit only for a single-solid STEP; a repeated PN needs an occurrence. The run refuses a part of two or more solids with `part_multiple_solids` and lists the `solid_index` and volume of each solid. Select one solid with `solid_index` or an `occurrence`, or fix the model so that the part is one solid.
- `occurrence`: quoted `"@id"` for a repeated part in a versioned assembly STEP.
- `solid_index`: optional 1-based index over the part solids of the STEP, in document order. Containers do not count. It is mutually exclusive with `part` and `occurrence`, and refused for record v2.
- `pose`: optional joint-value map copied from `worst_case.pose`.
- `evidence`: optional path to sweep `worst.step.json`; it binds STEP hash, full pose and occurrence.
- `fixed_faces`: required selector for constrained faces, using the same `indices` or `plane` forms as each `loads[].selector`.
- `loads`: non-empty list of `{selector, force: [Fx, Fy, Fz]}` in N; `load` optionally identifies its use-case load.
- `mesh_size`: positive maximum element size in mm.
- `required_safety_factor`: optional positive check threshold; a completed solve below it fails.
- `timeout_seconds`: optional positive whole-job limit, default 600. `null` takes the default.

## Record-bound material and evidence

For v2, use `material: {profile?, conditions: {conditioning, temperature, orientation?}, effective_isotropic?: {decision, justification}}` with `--hw hw --version vN`. A v2 case selects `part` or an `occurrence`; do not pass numeric material properties or `solid_index`. Legacy explicit `{E, nu, density, yield}` is accepted only for standalone/record-v1 exploratory cases and is never service evidence.

Optional case keys: `acceptance: {strength: REQ-###, stiffness: REQ-###}`, `mesh_convergence: {decision: DR-###}`, and `loads[].load`. The referenced requirement needs a structured criterion. Result blocks include material, evidence, mesh, binding and engineering packet.

A service load is covered only when the case's forces tagged with its `load` add up, as vectors, to at least its magnitude along its direction (at most 1% of the magnitude across it). The use case states the direction as the load's required `[x, y, z]` unit vector (norm 1 within 1e-6); no text form is read. The checker compares it with the case's forces as given, so it must be in the analysed part's model frame; it applies no frame transform, and a direction stated in the assembly frame (an `application` that names no part) is not converted. A reversed, rotated or cancelling force, or a direction that is not a unit vector, leaves the load uncovered.

Mesh convergence counts only as an approved, bound, converged study. The decision file `hw/decisions/DR-###.md` must have the exact first line `Status: APPROVED` and a fenced `mesh-convergence` YAML block:

```yaml
geometry_sha256: <sha256 of the case's STEP file>
material: {profile: <profile id>, sha256: <profile sha256 from the result's binding>}
supports: {indices: [1]}            # the case's fixed_faces
loads: [{selector: {indices: [2]}, force: [0, 0, -50]}]   # the case's forces per region, within 1e-6 of the force
mesh: {size_mm: 2, element_order: second}
criterion: {relative_change_max: 0.05}   # above 0, at most 0.1
refinements:                          # at least three, coarse to fine, one at the case's mesh_size
  - {size_mm: 4, max_von_mises_mpa: 78.0, max_displacement_mm: 0.190}
  - {size_mm: 2, max_von_mises_mpa: 82.5, max_displacement_mm: 0.198}
  - {size_mm: 1, max_von_mises_mpa: 83.3, max_displacement_mm: 0.199}
```

Both monitored quantities must move no more than `relative_change_max` between the two finest meshes and between the case's mesh and the finest. A missing, provisional, void or unbound study leaves convergence not demonstrated: strength and stiffness stay `unknown` and the result is not service evidence.

Completed results record reproducibility settings in `mesh_settings`: one Gmsh thread, random seed 1, zero 3D random factor, standard and Netgen mesh optimizers disabled, fixed elastic-plus-optimization high-order pass, and CalculiX SPOOLES solver. The `calculix_environment` setting gives ccx one thread in each of its three parallel stages. Each FreeCAD job uses a private FreeCAD profile, so user FEM and import preferences do not apply. `mesh_input_hashes` name the normalized `.geo` and BRep inputs. `mesh_quality` records the solved mesh's element count, its invalid-element count (0 in a solved result) and its smallest scaled Jacobian. The check samples each element at its nodes and integration points. High-order Gmsh optimization can perturb nodes across identical runs; repeat safety factors within 0.5% are this mesh noise, not evidence of a design difference.

`fea_static.py` publishes a v2 engineering packet at `hw/checks/<V>/fea-<case>/`; `stdout.check_bundle` names it. Only `engineering.strength`, `engineering.stiffness` and `engineering.anisotropy` are in static solver scope. Creep, fatigue, impact and life remain physical-validation obligations.

## Stop reaction

A hard stop's reaction comes from the modeled contact, never from a hand estimate. `stop_reaction.py` is a layout-stage producer. It covers `engineering.stop_reaction@IF-###`. The record requires that check of each critical structural interface that carries a load. Run it as follows:

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-analysis/scripts/stop_reaction.py --hw hw --version vN [--replace]
```

It publishes `hw/checks/<V>/stop-reaction/` (`result.json` and `evidence/stop-reaction.json`). Exit `0` means every obligation passes or is not applicable, `1` a failure or an unknown, `2` no result.

- The pivot is the single movable revolute joint between the interface's two parts, posed at `min` and `max`. A pair at zero distance there is a stop. An interface that names a part outside the assembly, or has no movable joint, is not a stop and is not applicable. A bare PN with two or more occurrences is ambiguous and unknown. Parts that overlap at a limit are a design defect and unknown, with the overlap depth. The same holds when the other part is farther than the contact tolerance at both limits. The tolerance is 0.15 mm, plus 1 nm (1e-6 mm) for rounding, or the largest clearance of a fit the interface declares as `<size> <HOLE/SHAFT>` when that is larger. The evidence records the tolerance, its source and the gap at each limit.
- The contact patch is the overlap of the touching, opposite-facing planar faces. Each outline point has the arm ((p − o) × n) · a. Here o is a point on the axis, a is the axis direction and n is the outward normal of the fixed part's face.
- The applied moment is the peak force of the interface's use-case loads times the moving part's farthest point from the axis. The record does not say where the force acts, so this arm is a conservative upper bound.
- The design reaction is the moment over the smallest arm. The evidence also holds the lower bound (moment over the largest arm), the uniform-pressure and compliant-stop figures (information only) and the pivot load bound, the reaction plus the applied force.
- `unbounded`: a smallest arm of zero or less leaves a rigid stop with no bounded reaction. The outcome is `unknown` with severity critical. Only an FEA contact case approved in a design record closes it.
- `understated`: a `functional.contact.load` below the design reaction fails with severity major. Records cite the check's bundle. Prose is not parsed.

Strength of the load path is a separate question for `fea_static.py`. This check covers planar contact only and no friction or dynamics.

## Exit codes

- `0`: analysis completed and any requested safety-factor threshold passed.
- `1`: analysis completed, but the required safety factor was not met.
- `2`: no result; invalid input, unavailable or failed FreeCAD job, timeout, malformed result, or infrastructure error.

## Solver failures

FreeCAD finds ccx and Gmsh in its own process, so an AppImage or a `FREECADCMD` wrapper works. It looks in this order:

1. `$CCX` or `$GMSH`, used as given.
2. FreeCAD's own lookup.
3. The directory of the resolved `freecadcmd`.
4. PATH.

A versioned run probes the tools first and records only their versions in the recipe. The job looks them up again. It refuses with `solver_unavailable` when it finds no tool. It refuses with `solver_changed` when a version differs from the recorded one. `preflight.py` reports the same lookup as its `fea-solvers` check. A failed solve names its cause in `error.code`, with evidence in `error.details` and the job's `traceback`:

- `mesh_invalid`: the high-order mesh holds elements with a nonpositive Jacobian, so the job refuses it before ccx runs. Details give the element count, the invalid count, the smallest scaled Jacobian and the Gmsh level. Reduce `mesh_size` where walls are thin or curvature is tight.
- `solver_crashed`: ccx died from a signal; details give signal, stage and degrees of freedom. The bundled SPOOLES index limit can fail on very large models; raise `mesh_size`.
- `solver_failed`: ccx stopped with `*ERROR` messages. A nonpositive Jacobian lists distorted second-order elements; lower `mesh_size`.
- `freecad_timeout`: details identify `import`, `mesh`, `solve` or `results`; raise `timeout_seconds` or adjust `mesh_size` for meshing.
- `load_not_applied`: a load with a nonzero force reaches no node of the CalculiX input, so the job refuses it before ccx runs. FreeCAD found no mesh element face on the selected faces. Details list each such load's constraint, force and node count (0). Check the face selector, or change `mesh_size`.
- `zero_result`: ccx returned a peak von Mises stress or a peak displacement of exactly 0 under a nonzero load. The load never reached the solved model, so the job refuses the result instead of passing it.

## Rules

MUST Match material properties to sourced grade, form, process state, orientation, conditioning and service temperature. Density or an unsourced name never supplies strength or another material's properties. See `references/material-evidence.md`.
MUST Check face selection, units, loads, constraints, contact assumptions, material evidence, mesh/convergence and solver warnings before relying on results.
MUST Treat results as static linear isotropic simulation, not physical validation or evidence of fatigue, creep, wear, thermal behavior, buckling, life or regulatory compliance.
NOT A yield safety factor is not a fatigue, buckling, physical test or certification margin.

```text
L1 ANALYSIS {case}: {PASS|FAIL|NO-RESULT}; {peak stress} MPa, {displacement} mm, {yield safety factor}
   Evidence -- {analysis bundle and engineering check packet paths}
```
CAP 120w clean · 220w with failures

