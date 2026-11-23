---
name: cad-design
description: Applies CAD appearance fidelity and declared-route manufacturing checks. Triggers on "make it look real", "detail the parts", or a PARTS/DETAIL gate.
user-invocable: false
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD Design Fidelity and Manufacturing Checks

TRIGGER
+ "make it look like a real product", "these parts are just blocks", "detail the parts"
+ an L1, L2, or L3 commit batch, or a PARTS/DETAIL gate
- fit classes, limits, or stack-ups → `cad-fits`
- joint posing, sweeps, or collisions → `cad-motion`
- L0 concept variants → `cad-concept`

## Workflow

1. LOAD `references/fidelity.md` for appearance decisions and `references/process-checks.md` for route obligations. Review recorded material, ordered route, capability, functional requirements and interfaces; infer no service or default route.
2. Work only on the assigned fidelity and source parts. Use the `model.py` contract for CAD implementation; a concept model is not a manufacturing plan.
3. On "commit batch", use `cad-render` to review the integrated build. Preserve scratch, record and gate ownership.
4. For PARTS run `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-design/scripts/form_check.py --hw hw --version vN --fidelity F2`. For DETAIL use the same command with `--fidelity F3`. This is the only form-check gate command; `--parts-dir` is legacy diagnostics only. Exit 0 means the form criteria and process packet pass, 1 means form failure or process packet not `pass`, 2 means no result. Use the version-bound `cad-motion` access/play producers and fulfill every packet obligation.
5. Compare the render with `references/fidelity.md`. Report appearance level separately from process and evidence coverage. A new support or inspection finding does not make an undocumented route pass.

## Rules

MUST Use each part's recorded material, route and capability. Unknown does not fall back to FDM.
MUST Treat form and envelope heuristics as diagnostics, not process or physical proof.
MUST Report missing capability, handler, required input or packet as UNKNOWN.
MUST Put every joint bore on its recorded assembly origin and axis; size bores from `cad-fits` output.
NOT Treat a render as a check result or detailed form as process evidence.

```text
L1 DESIGN {stage}: {PN count} parts at {lowest level}, form_check PASS|FAIL
   Findings -- {check, part, and value per form_check finding, only if any}
   Renders -- {absolute PNG paths and one line each on what they show}
```
CAP 80w clean · 160w with findings
