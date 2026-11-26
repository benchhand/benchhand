---
name: cad-record
description: Creates, validates and releases a hardware design record. Triggers on "create a CAD record", "validate this hardware project", or "release the design".
user-invocable: false
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD Design Record

TRIGGER
+ creating or validating a hardware project's `hw/` design record
+ "what goes in the CAD design record", "validate this hardware project", or preparing a release
- modeling a part or assembly → `cad-model`

## Workflow

1. For a new record, from the project root create `hw/` from the template:

   ```sh
   cp -R "$CAD_SKILLS/cad-record/assets/project-template/hw" hw
   ```

   Stop if `hw/` already exists; do not merge a template into a record. The template has record-file placeholders and no part folders. Add `hw/parts/<PN>/part.yaml` and `model.py` for each PN matching `^[A-Z]{2,5}-[0-9]{4}$`; record the EBOM, verification and decisions in their canonical paths from [layout.md](references/layout.md). Never copy `.cad/recheck.json`; `cad-recheck` generates it.
2. At GROUND, read [manufacturing selection](../cad-flow/references/manufacturing-selection.md). Follow its single decision table for service class and optical requirements.
3. In v2, keep requirements, preferences, assumptions and open questions separate; record each part's material/process intent, capability and evidence. Use the existing validators, selectors and producer checks; do not create a parallel workflow. LOAD [manufacturing-intent.md](references/manufacturing-intent.md) for the record contract and [evidence.md](references/evidence.md) for evidence and gates. Record bench bonding, potting or cure as a `sub_build` with a sourced `hold` and one `install` operation, as manufacturing-intent.md describes; do not bond a loose part in place.

   Record equipment the product mounts onto, but that is not part of the device, as `make_buy: existing`. A shaft, a mount face or a tripod are examples. It needs no SKU and no purchase. Ask the user first: "Is X equipment you already have that the device mounts onto, and not part of what we build? Where do its dimensions come from, and what range?" Then:

   - Record `existing.dimensions` as a known range (lower and upper) with its basis and sources.
   - Leave out `supplier` and `procurement`: it is not bought.
   - Leave out `manufacture` and `intent`: it is not built.
   - Join it to a product part with an interface.
   - Name it only as an assembly operation's `into`, never its `part`.
   - Record a reference body the same way, such as a harness bundle stub kept for clearance.
   - Name the stub as the harness-routing operation's `into`.

   Existing equipment stays in `assembly.step`, `assembly.glb` and every fit, sweep, collision and stack check. cad-model and cad-bom leave it out of `product.step`, `product.glb`, the BOM and the mass totals. A release ships `definition/product.step`, puts the in-context `assembly.step` under `context/`, and lists each interface with existing equipment under `mates_with`. One predicate decides this: `sourcing.delivered` in `scripts/sourcing.py`. Renders draw existing equipment as a ghost unless its `render` says otherwise.
4. Build record-derived guides after `vN` exists:

   ```sh
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/build_guide.py hw --version vN --out hw/docs
   ```

   Before release, regenerate with `--rev <rev> --date YYYY-MM-DD` using the UTC release date. Add `--replace` only when intentionally replacing a non-generated or edited guide. `assembly.yaml` drawing-operation `tools` and `consumables` are included only when the record states them; never infer tools or process settings.
5. Validate from the project root: `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/validate.py hw`. Exit 0 means no error findings, 1 means blocking findings, and 2 means usage or infrastructure error. Read the JSON report; warnings do not fail validation.
6. At DESIGN freeze, first run `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/evidence.py check --hw hw --version vN --stage design_freeze`. After all required evidence is accepted, run `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/evidence.py digest --hw hw --version vN`; copy its `evidence_sha256` into `product.yaml` `flow.frozen.evidence_sha256` beside the built version's `manifest_sha256` and the approving decision. No recorded selection blocks freeze and release.
7. Release only a frozen, validated version after the RELEASE gate approves. LOAD [release.md](references/release.md) for exact commands, refusal conditions, manifest and package contents.

## Layout

`layout.json` is the authority for paths, owners, output kinds and release routes. Read the generated [layout.md](references/layout.md) for project work; use [layout-api.md](references/layout-api.md) only when implementing or reviewing a CAD script that resolves layout paths.

MUST Put every record and generated output at the path its layout entry names; use `.cad-work/` beside `hw/` for scratch.
MUST Keep every blocking finding and required UNKNOWN unresolved until applicable evidence resolves it; a ledger status word is not evidence.
NOT Edit generated bundles, frozen versions, or releases in place.

LOAD [design-rules.md](references/design-rules.md) when repairing a coded design-rule finding.

LOAD [schema-bounds.md](references/schema-bounds.md) when adding a tunable to a schema or reading its bounds and defaults.

## Output

Report validator, evidence, freeze or release status from the command result and list cited record paths.
CAP 100w
