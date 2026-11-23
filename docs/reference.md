# Reference

Lookup tables for environment variables, installer flags, outcome rules, the gate decision key and agent roles. Every value comes from the file named beside it. For the per-script flags, run the script with `--help`.

## Environment variables

Only the string `1` turns a boolean flag on, as for `CAD_REQUIRE_FREECAD`.

| Variable | Default | Effect | Source |
|---|---|---|---|
| `CAD_SKILLS` | none | A placeholder in the skills' commands. No script reads it from the environment. See [`CAD_SKILLS`](#cad_skills). | `skills/cad-flow/SKILL.md` |
| `FREECADCMD` | first match of `freecadcmd` and `FreeCADCmd` on `PATH`, then `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`, then `~/.local/bin/freecadcmd` | Path to `freecadcmd`. Checked first when set. | `skills/cad-flow/scripts/preflight.py` |
| `CCX`, `GMSH` | FreeCAD's own lookup, then next to `freecadcmd`, then `PATH` | Path to the CalculiX and gmsh executables. A set value is used as given, with no fallback. | `skills/cad-flow/scripts/fea_solvers.py` |
| `CAD_OUTPUT` | `compact` on a terminal, `json` when piped | Output mode of the producers: `compact`, `json` or `text` (renders as `compact`). `--json` wins. `run.py` sets `compact` unless you set it. | `skills/cad-flow/scripts/run.py` |
| `CAD_REQUIRE_FREECAD` | off | `1` makes a FreeCAD-gated test fail instead of skip. CI sets it. | `docs/engineering.md` |
| `CAD_MODEL_BUILD_JOBS` | `min(cpus // 2, 4)`, at least 1 | Parallel part-build workers for `cad_model.py`, capped by the part count. Integer division. An empty or whitespace-only value counts as unset. Any other value that is not a positive integer is an error. | `skills/cad-model/scripts/cadmodel/runner.py` |
| `CAD_RENDER_RASTER_THREADS` | unset | `1` starts Chromium with `--num-raster-threads=1`. | `skills/cad-render/scripts/render.py` |
| `CAD_CALIBRATION` | off | Printer calibration. See [Feature flags](engineering.md#feature-flags). | `skills/cad-record/scripts/guidelines.py` |
| `CAD_FEA_METALS_NEAR_SUPPORT` | off | Form stand-in for metal ductility near a support. See [Feature flags](engineering.md#feature-flags). | `skills/cad-analysis/scripts/fea_conditions.py` |
| `SOURCE_DATE_EPOCH` | 315532800 (1980-01-01 UTC) | Date stamped in drawing PDFs, never the clock. | `skills/cad-drawings/scripts/sheet_export.py` |
| `CAD_VIEWER_SESSION` | state file under `XDG_STATE_HOME` | Overrides the viewer session-file path. | `skills/cad-viewer/scripts/cadviewer/session.py` |
| `CAD_VIEWER_SESSION_NAME` | none | Names the viewer session (same as `--session`). | `skills/cad-viewer/scripts/viewer.py` |
| `XDG_CACHE_HOME`, `XDG_STATE_HOME` | `~/.cache`, `~/.local/state` | Where preflight and the concept server cache, and where the viewer keeps its session. | `skills/cad-flow/scripts/preflight.py` |

## Installer flags

`python3 "$BENCHHAND/harness/install.py"` (`harness/install.py`):

| Flag | Effect |
|---|---|
| `--harness {claude,codex,opencode,kiro,all}` | Required. Which harness to install for. |
| `--user` or `--project DIR` | Mutually exclusive. `--project` defaults to the current directory. |
| `--dry-run` | Prints the exact paths and changes nothing. |
| `--uninstall` | Removes only the files that the installer recorded as its own. |
| `--tier-model [HARNESS:]TIER=MODEL` | Repeatable. Sets a model per agent tier. Needs PyYAML. See the README. |

Install paths (`harness_layout`):

| Harness | Project | User |
|---|---|---|
| Claude Code | `.claude/skills`, `.claude/agents` | `~/.claude/...` |
| Codex CLI | `.agents/skills`, `.codex/agents` | `~/.agents/skills`, `~/.codex/agents` |
| OpenCode | `.opencode/skills`, `agents`, `commands` | `~/.config/opencode/...` |
| Kiro CLI | `.kiro/skills`, `.kiro/agents` | `~/.kiro/...` |

### `CAD_SKILLS`

`CAD_SKILLS` is a placeholder in the commands of the skills, such as `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" ...`. The agent fills it in as the parent directory of the `cad-flow` skill folder (`skills/cad-flow/SKILL.md`). No script reads it from the environment. `cad-viewer` only prints a command that holds it (`skills/cad-viewer/scripts/cadviewer/session.py`).

When you run a script by hand, set it to the directory that holds the `cad-*` folders:

| Route | Directory |
|---|---|
| Claude Code, project install | `.claude/skills` |
| Claude Code, user install | `~/.claude/skills` |
| Codex CLI, project install | `.agents/skills` |
| Codex CLI, user install | `~/.agents/skills` |
| OpenCode, project install | `.opencode/skills` |
| OpenCode, user install | `~/.config/opencode/skills` |
| Kiro CLI, project install | `.kiro/skills` |
| Kiro CLI, user install | `~/.kiro/skills` |
| Claude Code plugin | the plugin's `skills/` directory (not confirmed here) |
| A checkout used directly | `"$BENCHHAND/skills"` |

## Outcome rules

Results for an evidence obligation (`skills/cad-record/scripts/evidence.py`, `RESULTS`): `pass`, `fail`, `waived`, `not_applicable`, `not_assessed`, `stale`, `planned`, `unknown`. Only `pass`, `waived` and `not_applicable` resolve an obligation.

- Within one obligation, the status comes from all its evidence (`_best` in `evidence.py`). A current `fail` stands, or becomes `waived` under a valid waiver. Otherwise a current `pass`, `waived` or `not_applicable` outranks `not_assessed`, `stale`, `planned` and `unknown`. `not_assessed` beside an explicit `unknown` stays `unknown`.
- Across a set of results, the worst one reports (`_worst`): `fail`, `stale`, `unknown`, `planned`, `not_assessed`, `waived`, `pass`, `not_applicable`. A producer packet is not evidence for one obligation; the first rule decides each obligation.
- A known `fail` blocks at a stage gate whenever it is known, due or not.
- An obligation is due at its own stage. From the design freeze on, every obligation is due.
- Before LAYOUT, an unresolved due obligation is a warning. From LAYOUT on it is an error.
- `not_assessed` is an info finding at a gate. It blocks nothing and never counts as a pass.
- A physical test (`physically_validated`) may stay `planned` until the release.
- At the release, only obligations in the release scope of the service class count. A failure outside that scope does not block it.
- At the release, a waiver resolves an obligation, with two limits. A `safety_critical` release takes no waiver. A waived physical test makes the release an unvalidated trial, and `production` and `safety_critical` refuse that. Both classes therefore need the physical test.

## Gate decision key

A gate decision in `hw/decisions/DR-###-slug.md` carries `gate: {from: <stage>, to: <stage>}` in its front matter, for example `gate: {from: layout, to: parts}`. `stamp.py --verify` accepts a stamp one stage behind `flow.stage` only when a `proposed` or `accepted` decision with that key and no `superseded-by` exists. Its date must be on or after the stamp's UTC day. A record without the key, or with another transition, is not that gate (`skills/cad-recheck/SKILL.md`).

## Agent roles

Sources are in `agents/`. The harness profiles are generated from them.

| Agent | Role |
|---|---|
| `cad-concept-designer` | Drafts one L0 concept variant as a checked three.js scene. Never edits `hw/`. |
| `cad-part-designer` | Designs one unit's parts in a scratch copy and delivers only its own part directories. |
| `cad-analyst` | Runs one L4 analysis case, read-only on `hw/`, and proposes verification rows. |
| `cad-sourcing-researcher` | Verifies purchased-part identity, ratings and supplier evidence. Read-only. |
| `cad-dfm-reviewer` | Challenges declared manufacturing routes with scoped evidence. Read-only. |
| `cad-mech-reviewer` | Challenges mechanical premises with scoped load, interface and simulation evidence. Read-only. |
| `cad-clerk` | Mechanical bookkeeping on the cheap tier. Issues no verdict or gate decision. |
