# Architecture

The sections below give the components of the Benchhand repository, their boundaries and the main flows. Each section names its source files. A statement marked "unverified" has no source in the repository. [engineering.md](engineering.md) holds the stack, the commands and the standards.

## What the repository ships

Benchhand is a set of agent skills and agent profiles for mechanical CAD (`README.md`). The skills guide a product from concept to a verified STEP AP242 release (`skills/cad-flow/SKILL.md`). The repository has no application server and no package install (`docs/engineering.md`). Users get the skills and profiles through `harness/install.py` or the Claude Code plugin (`README.md`, `.claude-plugin/`).

## Components

| Path | Role | Source |
|---|---|---|
| `skills/<name>/` | One skill: a `SKILL.md` plus optional `scripts/`, `tests/` and `references/` | `skills/*/SKILL.md` |
| `agents/` | Source of the seven neutral agent profiles | `agents/*.md`, `harness/generate.py` |
| `harness/` | Generates the harness profiles and installs skills and profiles | `harness/generate.py`, `harness/install.py` |
| `scripts/` | Repository tooling for CI and checks | `scripts/*.py` |
| `docs/` | User and engineering documents | `docs/` |
| `examples/lamp-arm/` | The example project | `README.md` |

Not every skill has every subdirectory. `cad-review-dfm` and `cad-review-mech` have only `SKILL.md`. `cad-bom` and `cad-fits` keep some test files at the skill root. Both test locations count: `skills/*/test_*.py` and `skills/*/tests/test_*.py` (`scripts/ci_shards.py`).

## Skills as separate script trees

Each skill is its own script tree. Most scripts carry a PEP 723 header with their own dependencies. They run through `uv run --script`. `skills/cad-flow/scripts/run.py` is the shared launcher. It reads the inline dependencies and uses an environment keyed by the dependency set (`README.md`, "Skill script launcher"). A skill refers to a sibling by a path built from `$CAD_SKILLS`, the directory that holds the installed `cad-*` folders (every `SKILL.md`). For example, `skills/cad-analysis/scripts/freecad_lookup.py` adds `cad-flow`'s scripts to `sys.path`. No checker enforces which skill may use which. That rule is unverified.

| Skill | Responsibility | Source |
|---|---|---|
| `cad-flow` | Stage sequence, gates, setup, preflight, the launcher and the FEA solver lookup | `skills/cad-flow/SKILL.md`, `scripts/` |
| `cad-record` | The `hw/` design record: schemas, validation, evidence, decisions, release | `skills/cad-record/SKILL.md` |
| `cad-concept` | L0 concept variants in a live three.js viewer | `skills/cad-concept/SKILL.md` |
| `cad-model` | Builds part scripts into versioned STEP, GLB and metrics; checks and diffs versions | `skills/cad-model/SKILL.md` |
| `cad-design` | Appearance fidelity (form check) and declared-route manufacturing checks | `skills/cad-design/SKILL.md` |
| `cad-motion` | Poses, sweeps and motion renders of jointed assemblies | `skills/cad-motion/SKILL.md` |
| `cad-render` | PBR renders and the visibility check | `skills/cad-render/SKILL.md` |
| `cad-viewer` | The OCP CAD Viewer panel for versions | `skills/cad-viewer/SKILL.md` |
| `cad-analysis` | Static linear FEA in FreeCAD with CalculiX | `skills/cad-analysis/SKILL.md` |
| `cad-bom` | EBOM roll-up into `bom.csv` | `skills/cad-bom/SKILL.md` |
| `cad-drawings` | Part and assembly drawings from FreeCAD TechDraw | `skills/cad-drawings/SKILL.md` |
| `cad-fits` | ISO 286 fits and tolerance stacks | `skills/cad-fits/SKILL.md` |
| `cad-recheck` | Recheck stamp that gates commits of `hw/` changes | `skills/cad-recheck/SKILL.md` |
| `cad-review-dfm`, `cad-review-mech` | Independent reviews; instructions only, no scripts | `skills/cad-review-*/SKILL.md` |

`cad-model` builds with CadQuery or build123d, in separate environments (`skills/cad-flow/SKILL.md`, rules; `skills/cad-model/scripts/run_cadquery.py`, `run_build123d.py`).

## cad-record owns the record

`cad-record` defines what the `hw/` record contains and checks it.

- Schemas: `skills/cad-record/schemas/*.schema.json` (product, requirements, assembly, interfaces, part, verification row, evidence run, release manifest and others).
- Layout table: `skills/cad-record/layout.json`, described in `skills/cad-record/references/layout.md`.
- Validator: `skills/cad-record/scripts/validate.py`. `schema_registry.py` loads the schemas as an offline registry. Its docstring names `validate.py`, `manufacturing_intent.py`, `check_packet.py` and `cad-fits` `stackup.py` as its users.
- Evidence and release: `evidence.py` runs and checks the evidence, `release.py` seals a release package under `hw/releases/<REV>/`.
- Output envelope: `emit.py` writes the `tool-result/1` envelope that producers print, and `unwrap()` reads it (`README.md`, "Output modes").

The lead agent owns the shared record. A worker writes only under `.cad-work/<unit>/`, and a part designer replaces its own `hw/parts/<PN>/` through `scratch.py deliver` (`skills/cad-flow/SKILL.md`, rules).

## Agent profiles and harnesses

- `agents/*.md` is the neutral source: `cad-analyst`, `cad-clerk`, `cad-concept-designer`, `cad-dfm-reviewer`, `cad-mech-reviewer`, `cad-part-designer` and `cad-sourcing-researcher`. Front matter holds name, description, tier, capabilities and skills.
- `harness/generate.py` renders them into `harness/claude`, `harness/codex`, `harness/opencode` and `harness/kiro`, plus one OpenCode command, `harness/opencode/commands/cad-flow.md`. `generate.py --check` byte-compares the committed output with the generator output. Edit the source and rerun the generator, never the output.
- Only `cad-clerk` may use the `cheap` tier (`CHEAP_ELIGIBLE` in `generate.py`).
- `harness/install.py` copies skills and profiles into a project or user scope. `skill_files()` ships `skills/cad-*` without `tests/` or `__pycache__` directories, `.pyc` files and symlinks. `harness_layout()` sets the target paths per harness.

## scripts/ is repository tooling

`scripts/` serves CI and review, not users. It holds the CI sharding (`ci_shards.py`), the docs-only gate (`docs_only.py`), the complexity ratchet, the import check (`import_check.py`), the type check (`typecheck.py`), `repro_check.py`, shared test helpers (`modload.py`) and guard tests such as `test_no_verify_guard.py` and `test_script_floors.py`. Shipped skill code does not import `harness/` or `scripts/`; the rule and its tests are in [engineering.md](engineering.md).

## The FreeCAD boundary

Host scripts run on Python 3.12 or later. FreeCAD jobs run inside `freecadcmd`, which bundles Python 3.11, so job code stays 3.11 compatible and imports only PyYAML and jsonschema (`harness/test-requirements-freecad.txt`).

- `scripts/freecad_job_modules.txt` lists the job scripts and the modules they import. `scripts/import_check.py --manifest` imports each entry on 3.11.
- A host script writes a job, starts `freecadcmd` with a timeout, and reads the result. Examples: `skills/cad-drawings/scripts/freecad_runtime.py` (`run_job`), `skills/cad-motion/scripts/freecad_assembly.py`, `skills/cad-analysis/scripts/fea_static.py`.
- `skills/cad-flow/scripts/fea_solvers.py` finds `ccx` and `gmsh` through FreeCAD's lookup, beside `freecadcmd`, or on `PATH`.
- FreeCAD finds `freecadcmd` on `PATH`, in the macOS app bundle, or through `$FREECADCMD` (`README.md`).

## Main flows

`cad-flow` leads the stages in this order: GROUND, INTENT, SPECIFY, L0 CONCEPT, VARIANT gate, L1 LAYOUT, LAYOUT freeze, L2 PARTS, PARTS gate, L3 DETAIL, DETAIL gate, BOM, L4 ANALYSIS, ANALYSIS gate, CRITIQUE, RECONCILE, DESIGN freeze, RELEASE gate, RELEASE (`skills/cad-flow/SKILL.md`, workflow step 3; `references/flow.md`). The user approves human gates, and the lead records each as a decision in `hw/decisions/`. The current stage lives in `hw/product.yaml` `flow`.

1. **Design to model.** Concept variants come from `cad-concept` (L0). From L1 each commit batch builds an immutable version with `cad_model.py run`. `cad-design` adds fidelity and manufacturing checks from L2, and `cad-fits` supplies fits and stacks.
2. **Check.** `evidence.py run --scope commit` runs the fail-fast order: `validate.py`, the form check, `cad_model.py check --changed-only`, the smoke sweep, then `cad-render` with its visibility check. After a provisional gate, `--scope full` runs every pair and the full sweep (`skills/cad-flow/SKILL.md`, step 5).
3. **Analysis.** At L4 the analyst agent runs the sweep, the stack-up, FEA at the worst pose and motion cases. `cad-motion` supplies the worst-case pose and `cad-analysis` runs the FEA.
4. **Drawings and BOM.** `cad-bom` rolls up `bom.csv`. At L3 and release `cad-drawings` drafts and publishes drawings through FreeCAD.
5. **Record and recheck.** The lead keeps gate evidence in `hw/`. `evidence.py check` writes the gate packet, and `cad-recheck` `stamp.py` stamps it into `hw/.cad/recheck.json`. `stamp.py --verify` runs before each commit of `hw/` changes: exit 2 blocks the commit (`skills/cad-recheck/SKILL.md`).
6. **Release.** `release.py` runs only after the RELEASE gate approves. The result is a CAD-only release: STEP AP242, drawings, EBOM, verification matrix and documentation. The release holds no slicer, CAM or machine output.

Delegation to the agent profiles is in the table of `skills/cad-flow/SKILL.md`. At most three writer units run at once.

## Unverified

- The rule about which skill may use which sibling (no enforcing check).
