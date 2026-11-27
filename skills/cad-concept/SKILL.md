---
name: cad-concept
description: Iterates 2-4 rough three.js concept variants in a live viewer, then exports the chosen one for CAD. Triggers on "sketch some concepts", "pick a variant".
user-invocable: false
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD Concept (L0)

Stage L0 CONCEPT of `cad-flow`: rough primitive variants the user compares, refines, merges or drops until the VARIANT gate. The gate produces a handoff manifest for L1 LAYOUT. Joints describe the mechanism at its nominal configuration and the handoff carries them to L1 LAYOUT.
Paths below are relative to this skill's directory.
+ "sketch some concepts", "show me a few options", "compare variants", "pick a variant"
+ `hw/product.yaml` has `flow.stage: concept`
- envelopes, interfaces, or a BOM skeleton -> L1 LAYOUT in `cad-flow`
- real geometry in CadQuery or build123d -> L2 PARTS in `cad-flow`

GATES
ASK which live variant passes the VARIANT gate. The lead records the user's named choice.

## Workflow

1. Read the approved use case, mandatory requirements, preferences, open questions and recorded selection. Variants propose geometry against that scope. LOAD `references/scene-format.md` before authoring a scene.
2. Draft 2-4 variants as distinct architecture/design proposals. Mark unknown material/process dependencies in each option; do not imply capability or final selection.
3. Run `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-concept/scripts/concept.py check hw/concept/<product-id>.scene.json` after each edit. Exit 0 means the scene contract passes.
4. Start one project-root viewer job for the complete scene directory:

   ```sh
   uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-concept/scripts/serve.py --project-root . --scene-dir hw/concept
   ```

   Read the first stdout JSON line and share its `url` with the user. Each saved edit reloads the open viewer.
5. Collect feedback. Use the existing variant table for shared scene changes only after user approval. The preview proves neither CAD validity nor manufacturability.
6. On "commit batch", run the check again and save one screenshot for each live variant; screenshots publish under `hw/concept/screenshots/`. Hand the batch to `cad-flow` for COMMIT.
7. At VARIANT, show unresolved process/material dependencies and ask the user to select. Record the approved `chosen` variant only after its `status: accepted` DR exists; then run `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-concept/scripts/concept.py handoff hw/concept/<product-id>.scene.json`.
   If the user keeps more than one variant, `handoff` still emits one manifest for one variant and creates no sibling. The comparison scene is not a record of any sibling: move it out of `hw/concept/` into `.cad-work/`, where it stays a working file and keeps every variant and the whole merge and drop history. Treat it as an archive, not as a scene: do not run `check`, `handoff` or the viewer on it. If you edit it later, for example to drop the variant that an earlier merge went into, it fails `check` with `merged into unknown variant`. Only the sibling scenes below are previewed and checked. Before you copy it, retire each variant the user dropped with its real reason, as the table below says. Then, in each sibling project (a sibling folder with its own `hw/`) for a kept variant:
   - Copy the comparison scene to the sibling's `hw/concept/<product-id>.scene.json`.
   - In the copy, delete every variant from `variants` except the kept one, and set `chosen` to its id.
   - In the copy, rebuild `retired` so that it holds only valid entries. Keep each `dropped` entry with its real reason. Keep a `merged` entry only when its `into` is the kept variant, because `check` refuses a merge into a variant that is not live (`retired.<id>: merged into unknown variant '<into>'`). Delete the other `merged` entries from the copy; the archive keeps them. For each other variant that a sibling keeps, add `{"id": "<id>", "label": "<label>", "disposition": "dropped", "reason": "kept in sibling project <name>"}`.
   - Run `concept.py check` on it. Exit 0 means the sibling scene is valid.
   - Record the approval as a DR with `status: accepted` in that sibling, and name the sibling in a decision record.
   - Run `concept.py handoff hw/concept/<product-id>.scene.json --out hw/concept/handoff.json`. The manifest binds to that sibling's scene by sha256.
8. After VARIANT passes or the session ends, tell the user the viewer URL is going away and stop the serving job with the harness's job/service handle; if none exists, start it as a background job with `&`, retain `$!`, and run `kill <pid>`. Report the handoff path to `cad-flow`.

| user says | operation |
|---|---|
| "make B taller", "try a 300 mm reach" | REFINE: edit that variant in place; ids unchanged |
| "B with A's base" | MERGE: edit B, then move A to `retired` with `merged`, `into: B`, reason |
| "drop C" | DROP: move C to `retired` with `dropped` and the user's reason |
| "one more option" | ADD: a new variant; at most 4 stay live |

The viewer pins three@0.186.1 by import map with SRI digests. `serve.py` may need network on first load, then serves verified modules from `~/.cache/cad-concept/vendor` (or `$XDG_CACHE_HOME`) offline. Its ready JSON reports the URL and `idle_timeout_min` (30 minutes by default).

## Rules

MUST Keep node ids stable across edits and variants: the same concept part carries the same id everywhere. The handoff and L1 key on them.
MUST Retire a variant into `retired` with a reason; never delete it.
MUST Fix every `concept.py check` error before showing the viewer.
MUST Treat optical requirements as hard selection constraints; follow [manufacturing-selection.md](../cad-flow/references/manufacturing-selection.md) for the one canonical optical-route rule.
MUST State material/process dependencies as proposals, not capability or manufacturing claims.
NOT Model fits, fasteners, fillets, or tolerances here; that is L3 DETAIL.
NOT Export a handoff before the user names the chosen variant and its approval DR is recorded.

```text
L1 CONCEPT: {scene path} -- {live variant count} live, {retired count} retired, gate PENDING|PASSED {chosen id}
   Viewer -- {URL while serving, or STOPPED}
   Handoff -- {path and node/joint counts, only after the gate}
```
CAP 80w

