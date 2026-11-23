# Reference

Lookup tables for environment variables, installer flags, outcome rules, the gate decision key and agent roles. Every value comes from the file named beside it. For the per-script flags, run the script with `--help`.

## Environment variables

Only the string `1` turns a boolean flag on, as for `CAD_REQUIRE_FREECAD`.

| Variable | Default | Effect | Source |
|---|---|---|---|
| `CAD_SKILLS` | none | Directory that holds the installed `cad-*` skill folders. The launcher commands in the skills build their paths from it. | `README.md` |
| `FREECADCMD` | looked up on `PATH`, then the macOS app bundle | Path to `freecadcmd` when FreeCAD is elsewhere. | `skills/cad-analysis/scripts/fea_static.py` |
| `CCX`, `GMSH` | looked up through FreeCAD, then `PATH` | Path to the CalculiX and gmsh executables. A set value is used as given, with no fallback. | `skills/cad-flow/scripts/fea_solvers.py` |
| `CAD_OUTPUT` | `compact` on a terminal, `json` when piped | Output mode of the producers: `compact`, `json` or `text` (renders as `compact`). `--json` wins. `run.py` sets `compact` unless you set it. | `skills/cad-flow/scripts/run.py` |
| `CAD_REQUIRE_FREECAD` | off | `1` makes a FreeCAD-gated test fail instead of skip. CI sets it. | `docs/engineering.md` |
| `CAD_MODEL_BUILD_JOBS` | `min(cpus / 2, 4)`, at least 1 | Parallel part-build workers for `cad_model.py`, capped by the part count. A value that is not a positive integer is an error. | `skills/cad-model/scripts/cadmodel/runner.py` |
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

`CAD_SKILLS` is the `skills` folder of the matching row.

## Outcome rules

Results for an evidence obligation (`skills/cad-record/scripts/evidence.py`, `RESULTS`): `pass`, `fail`, `waived`, `not_applicable`, `not_assessed`, `stale`, `planned`, `unknown`. Only `pass`, `waived` and `not_applicable` resolve an obligation.

- Severity order when results combine: `fail` over `unknown` over `not_assessed` over `pass`.
- A known `fail` blocks whenever it is known, due or not.
- An obligation is due at its own stage. From the design freeze on, every obligation is due.
- Before LAYOUT, an unresolved due obligation is a warning. From LAYOUT on it is an error.
- A physical test (`physically_validated`) may stay `planned` until the release. At the release, an unresolved physical test blocks it unless it is outside the release scope or waived with a recorded reason.

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
