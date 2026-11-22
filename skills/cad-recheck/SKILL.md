---
name: cad-recheck
description: Use when committing a batch of CAD hardware project changes at or after its recheck threshold.
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD Recheck

TRIGGER
+ A user commits a batch of design changes in a CAD hardware project.
+ A hardware project's current flow stage reaches its recheck threshold.
- Live preview or draft edits → do not run checks or write a stamp.

## Workflow

1. On a batch commit only, read the staged `hw/product.yaml` fields `flow.stage` and `flow.recheck_from`.
2. Before the threshold, build and render only; do not run checks or write a stamp.
3. At or after the threshold, run the producers for the changed inputs and all impacted dependents, then recompute the gate packet from the evidence producer:

   ```sh
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/evidence.py check --hw hw --version vN --stage <stage> --out .cad-work/recheck/evidence.json
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-recheck/scripts/stamp.py --stage <stage> --checks .cad-work/recheck/evidence.json [--passed]
   ```

   Stamp at the stage of the gate just passed. After a provisional gate writes its decision and advances `flow.stage`, rerun `evidence.py check --stage <gate stage>`. `product.yaml` is a record input, so the check refuses an earlier packet. `--verify` accepts a stamp at `flow.stage` or the stage just before it, never below `flow.recheck_from`. A stamp one stage behind also needs a `proposed` or `accepted` decision record in `hw/decisions` for that gate, with no `superseded-by`, whose front matter says `gate: {from: <stamped stage>, to: <flow.stage>}` (for example `gate: {from: layout, to: parts}`). A record without that key, or with another transition, the flow profile among them, is not that gate. Its date must be on or after the stamp's UTC day, so date every gate decision with the UTC calendar date.

   The evidence input MUST be the JSON packet that `evidence.py check` writes, or an `evidence.py run` report. A run report stamps with its `packet`. A run that stopped holds no packet, so it cannot stamp. A `cad_model.py check` result is never evidence. Include `--passed` only when every blocking obligation passes, and omit it for a failed gate. A record that is not version 2 cannot be stamped.
4. Stage the design changes and `hw/.cad/recheck.json`.

   Before each commit of `hw/` changes, run this from the project root:

   ```sh
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-recheck/scripts/stamp.py --verify
   ```

   Exit 0 permits the commit; exit 2 means do not commit. Fix stderr's reason, restamp, and verify again. Exit 0 means that the commit stages no `hw/` change, the stamp matches the staged `hw/` tree, or the staged stage is below `flow.recheck_from`.

`stamp.py` reads `hw/` at the git root by default.

For a project below the root, such as `examples/lamp-arm`, add `--hw <hw-directory>` to both commands. It names the `hw/` directory itself, as in `evidence.py --hw`. Pass `examples/lamp-arm/hw`, not `examples/lamp-arm`.

A relative path starts at the current directory. The project is the parent of that directory. Under it, `stamp.py` reads and writes only these paths:

- `hw/product.yaml`
- `hw/.cad/recheck.json`
- the staged `hw/` tree

At commit time, `--verify` reads the index. It skips a commit that stages no `hw/` change.

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-recheck/scripts/stamp.py --verify --hw examples/lamp-arm/hw
```

CI has nothing staged. Add `--committed` there. It checks the stamp against the `hw/` tree in HEAD and never skips.

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-recheck/scripts/stamp.py --verify --committed --hw examples/lamp-arm/hw
```

`stamp.py` parses staged YAML with cad-record's `yaml_loader.safe_load` (`yaml.safe_load` on libyaml's C parser) and declares `pyyaml>=6.0` for `uv run --script`; keep that PEP 723 dependency declaration when changing its dependencies.

## Changed parts and dependents

`hw/assembly.yaml` places each part definition (PN) as one or more occurrences. Each copy of a part is one more occurrence of its PN, and every occurrence of a PN shares that PN's per-part results. Select the checks to rerun from the change:

- A part definition changes: its model, its geometry parameters or its `model_frame`. Rerun:
  - that PN's per-part checks, once: model read-back, form, DFM, fits and the part drawing;
  - `cad-model check` interference, connection and clearance requirements (`pair_clearance`) for every pair that includes one of its occurrences;
  - the `cad-motion` sweeps that move one of its occurrences;
  - the FEA cases that select it by `part` or `occurrence`.
- One occurrence's `placement` changes. Rerun that occurrence's pair checks and the sweeps that include it. The PN's per-part checks stay valid.
- A joint's origin, axis, limits or endpoints change. Rerun these checks for its child occurrence and every occurrence below it in the joint tree:
  - their pair checks;
  - the sweeps that move them;
  - the FEA cases on a posed STEP.
- A joint's `coupling`, or a `profiles` or `mechanisms` entry it uses, changes. Every joint driven by it, directly or through other coupled joints, moves differently (cad-motion `coupling.py`). Rerun `cad-record` validation. Rerun these checks for those joints' child occurrences and every occurrence below them:
  - their pair checks;
  - every sweep whose independent joints drive them, with its worst-case STEP and `worst.step.json`;
  - the FEA cases that cite that `worst.step.json` as `evidence`.
- An occurrence is added or removed. Rerun the BOM row of its PN (the quantity is the occurrence count), the pair checks of a new occurrence, and the assembly drawings that list the PN.
- A material or process changes. Rerun that PN's per-part checks, the quantity-weighted assembly mass, and the FEA cases on that PN.
- A batch adds or changes a clearance requirement in `hw/interfaces.yaml` (`type: clearance` with `pair` and `min_clearance_mm`). Rerun `cad-record` validation, `cad-model check`, whose `pair_clearance` holds it at the nominal pose, and every sweep that moves either occurrence of the pair, which holds it at every sample.

The stamp hashes the whole staged `hw/` tree, so any staged change needs a new stamp. This list decides only which checks to rerun before that stamp.

The stamp command and `--verify` audit the complete `hw/` tree with cad-record's layout API. Any staged or untracked path whose layout finding blocks under `validate.blocking` prevents stamping or commit, including hidden generated subtrees under `parts/<PN>/`.

## Rules

MUST Re-run the checks and stamp after any staged `hw/` file changes.
MUST Preserve `hw/.cad/recheck.json` as generated project metadata, not as design input to its own tree hash.
NOT Run checks or stamp during live preview or draft feedback.
