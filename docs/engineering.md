# Engineering guide

This guide records the stack, layout, standards and checks of the Benchhand repository. It is derived from the repository files named in each section. A statement marked "unverified" has no source in the repository and needs an owner to confirm it.

## Stack and toolchain

| Item | Version or pin | Source |
|---|---|---|
| Host Python | 3.12 or later. Most PEP 723 headers say `>=3.12,<3.15`. | `README.md`, `scripts/test_script_floors.py` |
| FreeCAD job Python | 3.11, the Python that FreeCAD bundles. Code that `freecadcmd` runs stays 3.11 compatible. | `README.md`, `scripts/freecad_job_modules.txt` |
| uv | Runs every script and test. No version is pinned. | `.github/workflows/ci.yml` |
| FreeCAD | 1.1.4, checked by SHA-256 in CI | `.github/workflows/ci.yml`, `README.md` |
| CalculiX `ccx` | 2.23, bundled with FreeCAD. Recorded in solver manifests. | `skills/cad-analysis/tests/test_fea_static.py` |
| gmsh | 4.15.0, bundled with FreeCAD. Recorded in solver manifests. | `skills/cad-analysis/tests/test_fea_static.py` |
| CadQuery | `cadquery==2.8.0` with `cadquery-ocp==7.9.3.1.1` and `vtk==9.6.2` in the scripts that declare them | PEP 723 headers under `skills/*/scripts` |
| OCP without VTK | `cadquery-ocp-novtk==8.0.1.1.0` | PEP 723 headers under `skills/*/scripts` |
| build123d | `build123d==0.13.0`, `bd_warehouse==0.4.0` | `skills/cad-model/scripts/run_build123d.py` header |
| numpy | `numpy==2.5.5` | PEP 723 headers |
| trimesh, python-fcl | `trimesh==4.*`, `python-fcl==0.7.0.11` | PEP 723 headers |
| jsonschema | `>=4.23` in script headers. Test pin `4.26.0`. | `harness/test-requirements.txt` |
| PyYAML | `>=6.0` in script headers. Test pin `6.0.3`. | `harness/test-requirements.txt` |
| Playwright | `playwright==1.65.2`, with Chromium | `skills/cad-render` and `skills/cad-viewer` headers, `README.md` |
| Test-only pins | `defusedxml==0.7.1`, `hypothesis==6.170.15`, `click==8.5.1` | `harness/test-requirements.txt` |
| mypy, click for the type check | `mypy==2.4.1`, `click==8.5.1`, locked in `scripts/typecheck.py.lock` | `scripts/typecheck.py` header |
| ruff | `0.17.4` through `uvx` and in the ratchet header | `.github/workflows/ci.yml`, `scripts/complexity_ratchet.py` |
| reuse | `reuse[charset-normalizer]==6.3.0` | `README.md` |

Other pins in the script headers (`pillow`, `ocp_viewer`, `imageio-ffmpeg`, `lib3mf`, `lxml`, `networkx`) live with the script that uses them. Read the header of the script for the exact pin. Harness targets are Claude Code, Codex CLI, OpenCode and Kiro CLI (`README.md`).

There is no root `pyproject.toml` and no package install. Each script declares its dependencies in a PEP 723 header, and `uv run --script` builds the environment. CI runs tests with `--no-project --with-requirements harness/test-requirements*.txt`.

Release automation is release-please (`release-please-config.json`, `.github/workflows/release-please.yml`). Renovate keeps dependencies current (`.github/renovate.json5`).

## Layout and dependency direction

- `skills/<name>/` holds one skill. Each has `SKILL.md`, `scripts/` (code), `tests/` (unittest files and fixtures) and `references/` (documents the skill cites). The skills are `cad-flow`, `cad-record`, `cad-concept`, `cad-model`, `cad-design`, `cad-motion`, `cad-render`, `cad-viewer`, `cad-analysis`, `cad-bom`, `cad-drawings`, `cad-fits`, `cad-recheck`, `cad-review-dfm` and `cad-review-mech`.
- `agents/` holds the source of the seven agent profiles.
- `harness/` holds `generate.py`, which builds the per-harness profiles in `harness/claude`, `harness/codex`, `harness/opencode` and `harness/kiro`, and `install.py`, which copies skills and profiles into a project. Generated profiles are byte-compared with generator output, so edit the source and rerun `generate.py`, never the output (`REUSE.toml`).
- `scripts/` holds repository tooling: `ci_shards.py`, `complexity_ratchet.py`, `docs_only.py`, `import_check.py`, `typecheck.py`, `repro_check.py` and their tests.
- `docs/` holds user and engineering documents. `examples/` holds the lamp-arm example project.

Dependency direction, as far as the repository shows it:

- `harness/` and `scripts/` read skills as files. Skills do not import from `harness/` or `scripts/`. This is unverified as an enforced rule; no test checks it.
- Skills are separate script trees. `cad-record` supplies the record schemas, `emit.py` and the validators that other skills read. Skills reach a sibling through a path built from the skill folder (for example `freecad_lookup.py` in `cad-analysis` adds `cad-flow`'s scripts to `sys.path`). No import checker enforces which skill may import which. Unverified.
- Code that `freecadcmd` runs must import on Python 3.11 with only PyYAML and jsonschema. `scripts/freecad_job_modules.txt` lists those modules, and CI checks them (`scripts/import_check.py --manifest`).
- `scripts/test_docs_guard.py` and `scripts/docs_only.py` decide which paths no skill test reads, so a docs-only PR skips the skill shards.

## Standards and approved departures

Standards:

- Every source file carries an SPDX header (`MPL-2.0`, copyright "2026 Sjors Robroek"). Data and documents are covered by `REUSE.toml`, so Markdown files take no header. `reuse lint` enforces both.
- The CLA applies to every contribution (`CONTRIBUTING.md`).
- Host scripts need a `requires-python` floor of 3.12, except modules that FreeCAD runs on 3.11 (`scripts/test_script_floors.py`).
- Text files are opened with an explicit encoding (ruff `PLW1514`, CI).
- Complexity findings `C901`, `PLR0912` and `PLR0915` may not rise above `scripts/complexity-baseline.json`.
- Tests and scripts must not skip git hooks (`scripts/test_no_verify_guard.py`).
- Prose in Markdown follows the slopvac rules in `slopvac.toml`. Pull request bodies pass the strict profile.
- Type checking is incremental: `mypy.ini` lists the checked modules, and some are strict.

Approved departures. Each one has a tracking ID in the bead ledger.

| Departure | Why | ID |
|---|---|---|
| A hand-rolled spatial index in `skills/cad-model/scripts/cadmodel/agreement.py` (a cell grid with a dense box prefilter) for the GLB to STEP agreement check | It keeps the check numpy-only. Profiling on the pace host showed the prefilter dominates the compare time on large curved parts. A finer exact grid is proposed, still numpy-only. | benchhand-1tq (open) |
| Hand-rolled shell lexing in `scripts/test_no_verify_guard.py` | It was the quick way to scan shell text. It does not converge on edge cases. The user decided to drop it and keep the Python `ast` check plus YAML run values read with a YAML parser. `bashlex` was rejected: last release January 2023, GPLv3+ licence. | benchhand-ci0 (in progress) |
| `skills/cad-model/scripts/cadmodel/safe.py` and `occ_derived.py` stay Apache-2.0 inside the MPL-2.0 repository | They are adapted from AgentCAD 0.7.0 (Apache-2.0). `NOTICE` and `skills/cad-model/NOTICE` record the source and changes. `LICENSES/Apache-2.0.txt` holds the text. | none found in the repository; unverified |
| Heavy suites run on the pace host | The FreeCAD-gated and mutation suites are too slow for a laptop. Measurements in the repository cite pace (`skills/cad-render/scripts/parity_policy.py`). The rule itself is not written in the repository. | unverified |
| Provisional FEA constants in `skills/cad-analysis/scripts/fea_near_support.py`: `TOLERANCE = 0.05` (5% section-resultant residual) and `TORSION_FLOOR = 1e-3` (0.1% of F x L) | No sourced value exists yet. A section whose residual or torque exceeds them abstains. | benchhand-2dq |
| The wrought, sheet and rod form stands in for ductility in the strength-eligibility gate (`DUCTILE_FORMS` in `skills/cad-analysis/scripts/fea_conditions.py`) | Material profiles have no ductility field yet. | benchhand-2dq |

benchhand-2dq will replace the two constants with sourced values (or cite their basis) and add a ductility field that the gate reads instead of the form stand-in.

## Build, test and lint commands

All commands run from the repository root. They are copied from `README.md` and `.github/workflows/ci.yml`.

Light batch (no FreeCAD needed):

```sh
uv run --no-project --with-requirements harness/test-requirements-min.txt python -B -m unittest discover -s harness -p 'test_*.py'
uv run --no-project --python 3.12 --with-requirements harness/test-requirements-min.txt python -B -m unittest discover -s scripts -p 'test_*.py'
uvx ruff@0.17.4 check --select PLW1514 --preview skills/*/scripts
uv run --no-project --script scripts/complexity_ratchet.py
uv run --no-project --python 3.12 --with-requirements harness/test-requirements.txt python -B scripts/import_check.py
uv run --no-project --python 3.11 --with-requirements harness/test-requirements-freecad.txt python -B scripts/import_check.py --manifest
uv run --no-project --with-requirements harness/test-requirements-min.txt python -B harness/generate.py --check
uv run --no-project --locked --script scripts/typecheck.py
uvx --from 'reuse[charset-normalizer]==6.3.0' reuse lint
```

`harness/generate.py --check` compares the committed profiles with generator output. `scripts/typecheck.py` runs mypy over the modules in `mypy.ini`. The complexity ratchet reads `scripts/complexity-baseline.json`.

Skill tests. Run each test file on its own with the test pins:

```sh
uv run --no-project --with-requirements harness/test-requirements.txt \
  python -B -m unittest discover -s skills/<skill>/tests -p test_<name>.py
```

CI splits the files into the shards `motion`, `motion-b`, `cad-model`, `freecad-gated` and `rest` (`scripts/ci_shards.py`, the `SHARDS` text in `ci.yml`). The files in the `freecad-gated` shard need FreeCAD 1.1.4 on `PATH` or `FREECADCMD`. Without it those tests skip, and `CAD_REQUIRE_FREECAD=1` turns the skip into a failure, as CI does. The `freecad-gated` shard also runs the mutation corpus rate check: `uv run --script skills/cad-design/tests/mfg_corpus/rates.py --check-baseline`. A pull request that changes only paths no skill test reads skips the shards (`scripts/docs_only.py`).

Prose lint. Changed Markdown goes through slopvac:

```sh
slopvac lint docs/engineering.md --format json
```

A pull request body takes the strict profile:

```sh
VALE_GRAMMARS_PATH=<path to the grammar directory> slopvac lint <body file> --profile strict --format json
```

The grammar directory is local to the machine and is not in the repository. Unverified: where it lives.

Git hooks are not defined in the repository (no hook files found). Never pass `--no-verify`; the guard test fails on it.

## Unverified items

- The import rule between skills, `harness/` and `scripts/` (no enforcing test).
- The Apache-2.0 departure has no ledger ID.
- The pace-host rule for heavy suites has no source in the repository.
- The location of the Vale grammar directory.
- The uv version, and any git hook setup.
- The root `AGENTS.md` is absent from this commit; `CONTRIBUTING.md` and `README.md` stand in.
