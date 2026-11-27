# Engineering guide

This guide records the stack, layout, standards and checks of the Benchhand repository. It is derived from the repository files named in each section. A statement marked "unverified" has no source in the repository and needs an owner to confirm it.

## Stack and toolchain

| Item | Version or pin | Source |
|---|---|---|
| Host Python | 3.12 or later. Most PEP 723 headers say `>=3.12,<3.15`. | `README.md`, `scripts/test_script_floors.py` |
| FreeCAD job Python | 3.11, the Python that FreeCAD bundles. Code that `freecadcmd` runs stays 3.11 compatible. | `README.md`, `scripts/freecad_job_modules.txt` |
| uv | Runs the standalone skill scripts (through their PEP 723 headers), the tests and several tools. CI also runs `scripts/docs_only.py` and `scripts/ci_shards.py files` with plain `python3`. No uv version is pinned; developed with 0.13.x. | `.github/workflows/ci.yml` (lines 90 and 231) |
| FreeCAD | 1.1.4, checked by SHA-256 in CI. Verified on macOS: `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd --version` prints `FreeCAD 1.1.4 Revision: 20260928`. | `.github/workflows/ci.yml`, `README.md` |
| CalculiX `ccx` | 2.23, verified on macOS against FreeCAD 1.1.4: `/Applications/FreeCAD.app/Contents/Resources/bin/ccx -v` prints `This is Version 2.23`. The test fixtures use the same value in a mocked solver response. At runtime `skills/cad-flow/scripts/fea_solvers.py` probes the installed `ccx` (`tool_version`) and accepts an override through `$CCX`. | `skills/cad-analysis/tests/test_fea_static.py` |
| gmsh | 4.15.0, verified on macOS against FreeCAD 1.1.4: `/Applications/FreeCAD.app/Contents/Resources/bin/gmsh --version` prints `4.15.0`. The test fixtures use the same value in a mocked solver response. At runtime `skills/cad-flow/scripts/fea_solvers.py` probes the installed `gmsh` and accepts an override through `$GMSH`. | `skills/cad-analysis/tests/test_fea_static.py` |
| CadQuery | `cadquery==2.8.0` with `cadquery-ocp==7.9.3.1.1` and `vtk==9.6.2` in the scripts that declare them | PEP 723 headers under `skills/*/scripts` |
| OCP without VTK | `cadquery-ocp-novtk==8.0.1.1.0` | PEP 723 headers under `skills/*/scripts` |
| build123d | `build123d==0.13.0`, `bd_warehouse==0.4.0` | `skills/cad-model/scripts/run_build123d.py` header |
| numpy | `numpy==2.5.5` | PEP 723 headers |
| trimesh, python-fcl | `trimesh==4.*`, `python-fcl==0.7.0.11` | PEP 723 headers |
| jsonschema | `>=4.23` in script headers, `skills/cad-flow/scripts/setup.py` among them: it loads `flow_profile.py`, whose cad-record gate module imports it. Test pin `4.26.0`. | `harness/test-requirements.txt`, `skills/cad-flow/scripts/setup.py` header |
| PyYAML | `>=6.0` in script headers. Test pin `6.0.3`. `skills/cad-flow/scripts/preflight.py` declares it for `fea_profile.py`, which reads `flow.profile.fea` through cad-record's YAML loader; `preflight.py` imports that module inside `project_fea` only when it reads a project (a visible lazy import; `scripts/test_script_dependencies.py` lists it in `OPTIONAL_LOADS`, so a consumer that only does the FreeCAD lookup needs no PyYAML), so the detached warm-up and the cad-model runners need none. `harness/install.py` needs it only for `--tier-model` (rendering agents through `harness/generate.py`); a default install, including `--harness all`, runs without it. | `harness/test-requirements.txt`, `harness/install.py` |
| referencing | `>=0.28.4` in the script headers that import it, `skills/cad-flow/scripts/setup.py` among them (same reason as `jsonschema`). No test pin: `jsonschema` installs it. Scripts import it directly, so they declare it. | PEP 723 headers, `scripts/test_script_dependencies.py` |
| Playwright | `playwright==1.65.2`, with Chromium | `skills/cad-render` and `skills/cad-viewer` headers, `README.md` |
| Test-only pins | `defusedxml==0.7.1`, `hypothesis==6.170.15`, `click==8.5.1` | `harness/test-requirements.txt` |
| mypy, click for the type check | `mypy==2.4.1`, `click==8.5.1`, locked in `scripts/typecheck.py.lock` | `scripts/typecheck.py` header |
| ruff | `0.17.4` through `uvx` and in the ratchet header | `.github/workflows/ci.yml`, `scripts/complexity_ratchet.py` |
| reuse | `reuse[charset-normalizer]==6.3.0` | `README.md` |

Other pins in the script headers (`pillow`, `ocp_viewer`, `imageio-ffmpeg`, `lib3mf`, `lxml`, `networkx`) live with the script that uses them. Read the header of the script for the exact pin. Harness targets are Claude Code, Codex CLI, OpenCode and Kiro CLI (`README.md`).

There is no root `pyproject.toml` and no package install. Dependencies come from one of three places:

- Standalone skill scripts under `skills/*/scripts` declare them in a PEP 723 header, and `uv run --script` builds the environment.
- `scripts/complexity_ratchet.py`, `scripts/repro_check.py` and `scripts/typecheck.py` also carry a PEP 723 header. CI runs the first and the last with `uv run --no-project --script`.
- Tests and the other tooling have no header. These are `harness/generate.py`, `harness/install.py`, `scripts/ci_shards.py`, `scripts/docs_only.py`, `scripts/import_check.py`, `scripts/decision_fixture.py`, `scripts/modload.py` and `scripts/property_profile.py`. They run under the shared files `harness/test-requirements*.txt` (`--no-project --with-requirements`) or with plain Python. CI runs `docs_only.py` and `ci_shards.py files` with `python3` (`.github/workflows/ci.yml` lines 90 and 231). `harness/generate.py` imports PyYAML without a header, so its CI step passes `harness/test-requirements-min.txt`.

Release automation is release-please (`release-please-config.json`, `.github/workflows/release-please.yml`). Renovate keeps dependencies current (`.github/renovate.json5`).

release-please (type `simple`) bumps `CHANGELOG.md` and `.release-please-manifest.json`. Its `extra-files` entries (type `json`, with a JSONPath) make a release PR bump the other manifests to the same version:

| File | JSONPath | Read by |
|---|---|---|
| `.claude-plugin/plugin.json` | `$.version` | Claude Code plugins. Codex reads it as a Claude-compatible manifest. |
| `.claude-plugin/marketplace.json` | `$.plugins[?(@.name=='benchhand')].version` | Claude Code marketplace. The entry is picked by name. Claude reads `plugin.json` before `marketplace.json`, so drift in `plugin.json` hides a marketplace bump. Codex reads it as its legacy-compatible marketplace. |
| `package.json` | `$.version` | pi packages (`pi install git:github.com/benchhand/benchhand@vX.Y.Z`; without the tag pi installs the default branch). `pi.skills` is `./skills`. |

`scripts/test_release_manifests.py` fails when `extra-files` differs from this table, a version differs from the release manifest, or a declared path is missing. It also runs the pinned release-please: `scripts/release_tools/` holds a `package.json` and a committed `package-lock.json` for `release-please@17.6.0` (the version that action v5.0.0 bundles), and the test installs them with `npm ci`, so every package is checked against its lockfile hash. `scripts/release_tools/bump.cjs` builds the `simple` strategy from the config and applies its updaters to the files of a repository root. The tests assert that a release to 9.9.9 rewrites exactly the three versions in the real manifests, and in a fixture whose marketplace lists another plugin first. Without `node` and `npm` the test skips locally and fails when `CI` is set. CI provisions Node from `scripts/release_tools/.node-version` (24.22.3) with `actions/setup-node` v7.1.0, pinned to its SHA. Nothing opens a PR or calls GitHub. A new versioned manifest goes into the test's `VERSIONED` list and into `extra-files` together.

Git marketplace support by harness (checked 2026-11-23):

- Claude Code: yes, `/plugin marketplace add` reads `.claude-plugin/marketplace.json`.
- Codex CLI 0.171: `codex plugin marketplace add <repo>` registered the marketplace `benchhand` from the same file. That registers the marketplace only; the CLI has no plugin install command, so installing the plugin from it is unverified. No Codex-specific manifest is needed. Source: <https://developers.openai.com/codex/plugins/build>.
- pi 1.6.0: yes, as a package. `pi install -l -a <repo>` loaded all 15 `cad-*` skills. No prompt template is declared yet: #323 owns `harness/pi/prompts`, so add `pi.prompts` once it lands. Source: pi `docs/packages.md`.
- OpenCode and Kiro CLI: no git marketplace. OpenCode takes plugin packages (JS) and skill catalogs (`skills` config, `index.json`); Kiro imports a skill from a repository URL. Both keep the installer route. Sources: <https://opencode.ai/docs/plugins/>, <https://kiro.dev/docs/skills/>.

## Layout and dependency direction

The directory layout, the components and the flows are in [architecture.md](architecture.md). This section keeps the import rules.

Dependency direction, as far as the repository shows it:

- Shipped skill code never imports `harness/` or `scripts/`. `skill_files()` in `harness/install.py` (lines 58 to 69) copies `skills/cad-*` without `tests/` or `__pycache__`, so the installed skills carry no path to either directory. `rg -n "harness|scripts/|modload" skills/*/scripts` finds only comments and printed or run commands.
- Tests may import them, through `scripts/modload.py`, the shared test helper (its docstring: "Shared test helpers"). Two cases:
  - `skills/cad-bom/tests/test_bom.py` (lines 25 and 26) puts `scripts/` on `sys.path` and imports `result_payload` from `scripts/modload.py`.
  - `skills/cad-model/tests/test_cad_model.py` (lines 3413 to 3416) loads `harness/install.py` with `importlib` on purpose.
- Every test file sits under a skill's `tests/` directory, which the installer skips. `ShippedSkillFilesTests` in `harness/test_install.py` fails when `skill_files()` returns a test file or a Python file that imports `harness` or `scripts`, or climbs to them with `parents[n] / "scripts"` on `sys.path`.
- Skills are separate script trees. `cad-record` supplies the record schemas, `emit.py` and the validators that other skills read. Skills reach a sibling through a path built from the skill folder (for example `freecad_lookup.py` in `cad-analysis` adds `cad-flow`'s scripts to `sys.path`). No import checker enforces which skill may import which. Unverified.
- The flow profile follows that direction. `cad-record/scripts/flow_profile_gate.py` is a pure function of the parsed product, the part records and an `approval` callable (it never reads the work tree and takes no `hw` path). `cad-flow/scripts/flow_profile.py` holds the questions, the `record` and `check` commands, loads the gate and judges the cited decision with `validate.py` `approval_problem`. `cad-record` never imports `cad-flow`.
- `skills/cad-record/scripts/shared_module.py` `load_shared` is the one loader for a sibling module reached by path, once per name and safe for worker threads. `assembly_operations.py` puts its own folder on `sys.path` (appended, so no earlier folder is shadowed) and loads `tool_envelopes.py` through it. `cad-drawings` `drawing_intent.py` appends `cad-record`'s scripts folder and loads `cad-fits` through it. `cad-record` loads nothing from `cad-drawings` for it.
- `skills/cad-record/scripts/stages.py` owns the stage list: it reads the stage enum from `product.schema.json` and imports only the standard library. `validate.py` and `decision_records.py` both load it by path, as `cad_record_stages`, and neither imports the other. This is the sibling-module convention. A test that copies `validate.py` to a temporary folder must copy `stages.py` beside it (`test_reports_info_when_concept_handoff_schema_is_missing`). `code_shas` hashes it only for a producer that reaches `decision_records.py`, as `fea_conditions.py` does; `validate.py` is `GATE_ONLY`, so a closure leaves it and `stages.py` out.
- Code that `freecadcmd` runs must import on Python 3.11 with only PyYAML and jsonschema. `scripts/freecad_job_modules.txt` lists those modules, and CI checks them (`scripts/import_check.py --manifest`).
- `scripts/test_docs_guard.py` and `scripts/docs_only.py` decide which paths no skill test reads, so a docs-only PR skips the skill shards.

## Standards and approved departures

Standards:

- Every source file carries an SPDX header (`MPL-2.0`, copyright "2026 Sjors Robroek"). Data and documents are covered by `REUSE.toml`, so Markdown files take no header. `reuse lint` enforces both.
- The CLA applies to every contribution (`CONTRIBUTING.md`).
- Host scripts need a `requires-python` floor of 3.12, except modules that FreeCAD runs on 3.11 (`scripts/test_script_floors.py`).
- A script's PEP 723 `dependencies` cover the third-party packages that its code, and the repository files it imports or loads, import. `scripts/test_script_dependencies.py` derives them with `ast` over `skills/*/scripts/**/*.py`. Its `OPTIONAL_LOADS` list names the reaches it skips, with the reason for each.
- Text files are opened with an explicit encoding (ruff `PLW1514`, CI).
- Complexity findings `C901`, `PLR0912` and `PLR0915` may not rise above `scripts/complexity-baseline.json`.
- Tests and scripts must not skip git hooks (`scripts/test_no_verify_guard.py`).
- Type checking is incremental: `mypy.ini` lists the checked modules, and some are strict.
- Printer calibration is never required (benchhand-c7q.30). A process check without a calibration judges against the shipped or project guideline value (`process_checks/core.py` `Context.governing`) and says the result is uncalibrated. A printed stack-up contributor without a calibration is qualified by the guideline deviation interval (`cad-fits/scripts/stackup.py` `guideline_deviation`), and the stack recipe seals `basis_revision` so an older bundle is never reused. A calibration overrides the guideline value; a calibration that does not cover the feature (an undersized warp coupon) is rejected before the guideline is resolved. When neither exists, a printer (FDM or resin) check or stack contributor is not assessed with reason `no_guideline` (`process_checks/additive.py` `_no_basis`; also the ream or finishing allowance after a printed operation, `secondary.py` `_cleanup`): a minor finding that blocks no gate and is never a pass. A known failure in the same obligation, a calibrated miss included, still fails it. A critical feature passes only when its deviation fits the tolerance it states, and a receiving bore no critical surface names stays UNKNOWN. A critical-surface criterion that no check measures (a thickness, depth or width, for example) is a check-coverage gap, not a missing printer basis: an explicit `criterion_unchecked` finding that leaves the obligation UNKNOWN, never a pass. Not-assessed (non-blocking) is only for a missing printer basis. A `between` band of a dimensional size narrower than the spread of the governing deviation fails, since no model size holds it. A size is a property listed in `SIZE_PROPERTIES` (`process_checks/additive.py`) stated in a length unit; a name is never read as a size from its suffix, and roughness, deviation and flatness properties are never sizes. Every criterion a feature's checks do not consume, a bore's included, goes through one of these two rules. A record gap (a bore with no diameter criterion) is checked before any basis lookup, so it stays UNKNOWN. Other processes keep UNKNOWN, since their evidence (a tool maker's draft, a joint study, an instrument uncertainty) is not a printer calibration. A missing guideline value is added with a cited source, never a default. `fdm.accuracy` holds the desktop FDM dimensional and hole deviation (-0.5..0.5 mm, Protolabs Network) for features up to 100 mm (`applies_to.max_size`), where the cited 0.5 mm floor governs the ±0.5% figure. A larger feature, or one of unknown size, takes no value from it. A critical feature's size is bounded by its part's box diagonal. Discovery refuses a `max_size` that is not a finite positive length. No shipped set states warp, profile deviation, elephant foot or bed contact area: the design guides checked (Protolabs Network, Bambu Lab Wiki) give warp only as qualitative advice (avoid large flat areas, use an enclosure for ASA).

Approved departures. The tracking IDs come from the bead ledger, which the repository does not hold, so the ledger facts below are unverified.

| Departure | Why | ID |
|---|---|---|
| A hand-written spatial index in `skills/cad-model/scripts/cadmodel/agreement.py` (`_Surface`, a uniform grid of cells with a box prefilter in `within`) for the GLB to STEP agreement check | The module imports numpy and abstains without it, so the index keeps to numpy. The profiling result, the proposed finer grid and the bead status are unverified (ledger only). | benchhand-1tq |
| Hand-written shell lexing in `scripts/test_no_verify_guard.py` (a `shlex` based word splitter near line 134) | The plan to replace it, the reasons and the bead status are unverified (ledger only). | benchhand-ci0 |
| `skills/cad-model/scripts/cadmodel/safe.py` and `occ_derived.py` stay Apache-2.0 inside the MPL-2.0 repository | They are adapted from AgentCAD 0.7.0 (Apache-2.0). `NOTICE` and `skills/cad-model/NOTICE` record the source and changes. `LICENSES/Apache-2.0.txt` holds the text. | benchhand-76q |
| FEA thresholds in `skills/cad-analysis/scripts/fea_near_support.py`: `TOLERANCE = 0.05` (5% section-resultant residual) and `TORSION_FLOOR = 1e-3` (0.1% of F x L) | Recorded as engineering judgment (decision U-2, 20 Nov). The user chose these values on 20 Nov 2026: `TOLERANCE` stays at 5% and `TORSION_FLOOR` stays at 0.1% of F x L. A section that misses a gate abstains, never a pass or a fail. Neither ASME VIII-2 Annex 5-A nor EN 13445-3 gives a number. | benchhand-2dq |
| Workspace standard: owned API and output contracts are written in TypeSpec. Departure: for each CLI output or record that has a registered schema, JSON Schema (Draft 2020-12, as the existing schemas declare) is the source of truth. `scripts/gen_api.py` discovers every `*.schema.json` in the registered directories (`SCHEMA_DIRS`, `scripts/gen_api.py:35`) and generates the Python types and `docs/schemas.md` from them. Not covered yet: some CLI outputs have no schema, for example `flow_profile.py check` (`skills/cad-flow/scripts/flow_profile.py:465`). New outputs require a schema: add or extend a `*.schema.json` in a directory that `gen_api.py` registers in `SCHEMA_DIRS` (`scripts/gen_api.py:35`). Use the owning skill's existing registered schema directory (`skills/cad-record/schemas` or `skills/cad-concept/assets/schemas`). A new directory must be registered there first, because `gen_api.py` ignores unregistered ones. Then regenerate as described in the Schema API generator section (line 186), and test the CLI output by parsing its stdout and validating it against the schema (example: `skills/cad-record/tests/test_sarif.py:425-433`). | Two consumers already share the format: the Python skills (`skills/cad-concept/scripts/concept.py:363`) and the JS viewer (`skills/cad-concept/assets/viewer/viewer.js:463`). The schemas are already checked by tests and by `gen_api.py --check`. TypeSpec would add Node to schema generation; CI already has Node for release tooling (line 47). Reversal trigger: a future need for generated bindings shared across languages, which TypeSpec would serve. | benchhand-6im |
| `user-invocable: false` in the front matter of the 12 hidden helper `skills/*/SKILL.md` files, a Claude Code key that is not an Agent Skills spec key, so a strict spec validator rejects those files | The Claude plugin route reads `skills/` directly, so the key in the shared file is what hides the helpers from the `/` menu. `harness/install.py` removes the line (plain text, no PyYAML) for every harness but Claude. `harness/test_skill_visibility.py` limits the front matter to spec keys plus this one. | benchhand-lns |

## Project-start profile gate

The user's answers to the project-start questions live in `product.yaml` `flow.profile`, with the `DR-###` decision that holds them. From SPECIFY on, a missing profile, one whose cited decision is not `accepted`, or an applicable question with no `asked` answer blocks.

- Owner: `skills/cad-record/scripts/flow_profile_gate.py`, a pure function of the parsed product, the part records and an `approval` callable. It reads no file. `skills/cad-flow/scripts/flow_profile.py` owns the questions and the `record` and `check` commands.
- Callers, each of which supplies the records and the approval:
  - `validate.py` `check_flow_profile` reads the work tree. The `flow.profile` and `flow.profile_unquoted` findings are errors; a `flow.conflict` runs only on schema-valid documents, is an error from LAYOUT on and for the safety rule at any stage, and is a warning before LAYOUT. Only an authored FEA case (a `case.yaml` with `step`) counts as one that needs FEA; a stack-up case in the same folder does not.
  - `cad_model.py run` (`cadmodel/build.py` `require_profile`) reads the record snapshot it builds from, checks at LAYOUT at the earliest and exits 2 on a block.
  - `stamp.py --verify` (`require_profile`) checks before the `flow.recheck_from` early return, so a stage below the threshold is still checked once SPECIFY is due.
- `validate.py` is listed in `GATE_ONLY` in `skills/cad-motion/scripts/code_shas.py`: a gate that decides refuse or allow, and changes no output, stays out of the manufacturing output key. `test_mfg_key_location` `OutputKeyInputTests` pins both sides: a validator edit leaves the key, an edit to `manufacturing_intent.py` changes it.
- The commit check reads the index only: the product, the part records (`tree_parts`) and the decision records (`tree_decisions`) come from the staged tree, or from HEAD with `--committed`. A decision that is accepted in the work tree but proposed in the index does not pass. Do not add a work-tree read to it.

## Service-class scope

`skills/cad-record/scripts/project_scope.py` holds the one table of what each service class owes. `RULES` gives each rule one disposition per class:

- `required`
- `check_before_use`
- `advisory`
- `out_of_scope`

The module:

- Owner: `project_scope.py`. It loads `manufacturing_intent.py`, `flow_profile_gate.py` and `layout.py` by path. It reads no project file. The modules it loads read the shipped product schema. `ProjectScope.of(record)` reads the service class and the flow profile once. An unknown or unlisted class resolves every rule to `required`.
- An obligation rule selects families by prefix and the evidence levels it covers. The first matching rule decides, so a narrower prefix (`optical.route`, `motion.play`) comes before its family. The last rule covers every family at every level, a level outside the schema included.
- `required_checks` takes no scope input. The obligation list and `expected_sha256` stay the same for every class.
- `SERVICE_POLICY` moved here from `evidence.py`, which re-exports it. It keeps the release columns of a class: the levels it may claim and whether it allows an unvalidated trial.
- `evidence.py` `in_release_scope` is `project_scope.in_release_scope`: an obligation is in a release's scope unless its disposition is `out_of_scope`.
- Gates before the release read the scope too. `evidence.py` `obligation_gate` takes the project's `ProjectScope`. Before the release, an `out_of_scope` obligation blocks nothing. A `gate.out_of_scope` info row names it, with its rule and reason, wherever the gate would otherwise have judged it. Its result is never rewritten, so an unknown stays unknown, and the release never claims it. An `advisory` or `check_before_use` obligation still gates as `required` until later T3 changes (epic benchhand-vwo).
- The evidence packet is version 2. It adds two fields:
  - `statuses[*].scope`: the disposition, the rule that decides it and the reason;
  - `scope_rules_sha256`: the digest of the rules that judged the packet.

  `packet_problems` reads versions 1 and 2. A version 1 stamp predates the scope, and it still verifies: `packet_blocking` recomputes its gate under the current scope. A version 2 packet whose `scope_rules_sha256` differs from the record's is stale and never stamps.
- The packet contract is `skills/cad-record/schemas/evidence-packet.schema.json` (JSON Schema 2020-12, packet version 2). `gen_api.py` generates its types and its `docs/schemas.md` section. A new stamp (cad-recheck `stamp.py` `require_sealable_packet`) seals version 2 only, and only a packet that follows the schema. Any other packet gets `stamp.packet_version` or `stamp.packet_schema` and exit 1.
- `rules_sha256(service_class)` is the digest of `RULES` and the class, through `layout.recipe_sha256`, the record's canonical JSON.
- Entry point: `project_scope.py rules [--class C]` prints, for each class (or the one given, or `unknown`), each rule's disposition with the reason. It prints text only, so it has no schema. It also prints which rule decides each family that two rules name, at which evidence levels (the first matching rule wins), and what the gates do with each disposition. That text describes `evidence.obligation_gate`; `test_project_scope.py` runs the real ANALYSIS, design freeze and release gates against it, so a gate change fails the test until the wording and `tests/fixtures/rules/` follow. A rule a class does not owe prints as `not owed at any gate`: every gate skips it, and a gate before the release names it in a `gate.out_of_scope` info row. Flow docs invoke it as `uv run --script "$CAD_SKILLS/cad-record/scripts/project_scope.py" rules`; `scripts/test_docs_guard.py` (`ServiceClassDocTests`) keeps a service-class table out of them.
- Tests: `skills/cad-record/tests/test_project_scope.py`, golden text per class in `fixtures/rules/`. `fixtures/release_scope_5dcd2c92.json` holds the release scope that `SERVICE_POLICY` gave at 5dcd2c92, before the move. It covers every class and schema level. It lists every fixed family, with one representative for each open-ended set: one unlisted route kind and one unlisted family. A rule change that alters release scope fails that test. Only an approved decision to change the scope justifies regenerating the fixture.

## Hiding and ghosting parts in renders

`render.py` and `motion.py render` take repeatable `--hide`, `--ghost` and `--show` (a PN or an occurrence id). The optional `part.yaml` field `render: shown|ghost|hidden` (`part.schema.json`) sets a part's default, and the flags override it.

- Owner: `render_states` in `skills/cad-render/scripts/render.py` resolves the lists; `skills/cad-render/assets/renderer/render.js` draws them. A ghost reuses the ghosted-context material of the close-up fallback (`ghostMaterial`) and adds edge lines. The viewer's `parts` action (`skills/cad-viewer`) is a separate browser-side tool and is not reused.
- A ghost is never an occluder: the mask pass and the floor bake leave it out, and requirement visibility counts a ghosted or hidden window part as see-through. A hidden visibility target is an error.
- `render.json` and `motion-render.json` list `hidden` and `ghosted`. Both enter the cache key and every view's `render_inputs`, so a change re-renders. `motion.py` adds them to the bundle recipe.
- An existing part (`make_buy: existing`) with no `render` value defaults to ghost (`sourcing.delivered`).

## Existing equipment

`make_buy: existing` marks equipment the product mounts onto or interfaces with that is not part of the device, such as a shaft or a mount face. It has no SKU, no purchase and no manufacture.

- Contract: `skills/cad-record/schemas/part.schema.json` (`make_buy`, `$defs/existing`). An existing part carries `existing.dimensions` as a known range (lower and upper, no single value) with one of the bases in `$defs/existing`. The schema refuses `supplier` and `procurement` on it, and also `manufacture` and `intent`.
- `manufacturing_intent.py`: its sourcing is `existing` (`Part.existing`). Its route is not checked against its sourcing. A version 2 record does not ask it for `intent`. `required_checks` gives it no obligation, so no process or purchase check runs on it.
- `selection.py`: `assess` drops a candidate part entry that names it before assessment, so selection never ranks it or proposes a `make_buy` for it. A candidate left with no part entries is dropped too. `manufacturing_intent.py` does not check the material or capability references of such an entry.
- `impact.py`: `existing.dimensions` is part of its `geometry` fingerprint domain, and the artifacts that the range's sources cite are part of `source`. A range change makes its `cad.*` and `interface.*` evidence stale. It has no `purchase.*` check to go stale.
- `cad-design` `form_check.py`: its process is `existing`. It gets B-rep validity, the STEP read-back and the joint check, but no minimum-wall scan and no form heuristic. The joint check reads every part's mating geometry the same way, a bore, a shaft or a flat face contact, whatever its sourcing.
- `validate.py` `check_existing`: `existing.interface_missing` when no interface joins it to a product part, and `existing.installed` when an assembly operation names it as `part`. It may be an operation's `into`. An `@occurrence` end or part counts as the part number the assembly inventory resolves it to. `validate.py` and `cad-design` `process_checks/purchased.py` find its interfaces with `manufacturing_intent.interfaces_of`.
- `skills/cad-record/scripts/sourcing.py` owns the delivered-product rule:
  - `sourcing.delivered` is false only for `make_buy: existing`. Every product export asks it, and no other code tests `make_buy` for this.
  - `sourcing.group` names the export group: `made`, `purchased` or `existing`.
  - It holds the export file names (`PRODUCT_STEP`, `PRODUCT_GLB`, `RELEASE_PRODUCT_STEP`, `RELEASE_CONTEXT_STEP`). `layout.json` lists them too, so a rename is one edit in each file.
  - The record has no other reference-only kind. The author records a reference body, such as a harness bundle stub, as existing equipment.
- cad-model `build.py`:
  - `assembly.step` and `assembly.glb` stay the in-context scene. Every check, sweep, render and viewer reads them.
  - `product.step` and `product.glb` hold the delivered occurrences only, with the same placements and definitions.
  - The `metrics.json` totals count the delivered occurrences only.
  - `product.step` puts each occurrence under a `made` or `purchased` group product (`pose.write_assembly_step` `groups`), and `product.glb` takes those group nodes from it.
  - `mesh.group_glb` puts the occurrence nodes of `assembly.glb` under `made`, `purchased` and `existing` nodes. It adds the group nodes at the end, so the part-map indices hold.
  - `assembly.step` keeps its flat tree. Its readers build occurrence paths from that tree (`pose.read_assembly_step`). Bead benchhand-pcy.4 groups it once they resolve occurrences by name.
- cad-model `check.py` `product_exports`:
  - The frozen part records decide which occurrences of `assembly.step` ship (`delivered_occurrences`, through `sourcing.delivered`). The manifest's `product.occurrences` must list the same set; the check never takes it on trust.
  - `product_subset` matches components by occurrence name, never by tree path. Each delivered occurrence ships once, with the PN, definition (name and read-back volume) and placement that `assembly.step` gives it.
  - In `product.glb`, each delivered occurrence has exactly one node reachable from the scene with a mesh in its subtree. The walk descends below an occurrence node too, so a nested node with the same name is a duplicate. An occurrence's mesh search stops at the next occurrence node, which owns its own mesh. Any other reachable mesh node fails. A malformed node tree fails without a crash: a bad or boolean scene index, a bad root or child index, or a node the scene reaches more than once.
  - A STEP product id or name, or a GLB node name, that starts with an existing PN fails. So does a leaf product without a PN in `product.step`: `pose.read_assembly_step` lists such leaves under `unnumbered`. Group products without a PN, such as `made`, are allowed.
  - It reads the exact paths `release.py` ships (`sourcing.PRODUCT_STEP`, `PRODUCT_GLB`). It skips only a version with neither file. A missing export, a manifest without its `product` block and an unreadable export fail.
  - Its verdict is the product-level obligation `cad.product_exports@product` (`manufacturing_intent.required_checks`), which every record owes, a catalogue-only one included. A version without product exports gets `not_applicable`. `release.py` refuses a version that seals `product.step` unless that outcome is `pass`.
  - `check` parses the frozen snapshot once (`build.read_snapshot`) for both the subset and the published packet.
- cad-drawings `assembly_check.reconcile` takes the existing PNs (from the part records through `sourcing.delivered`). Existing equipment needs no parts-list item, balloon or BOM row, and an item for it is refused. It stays an operation target: a note names it by its product name. `assembly_techdraw_job.py` draws it in a view of its own at the same direction and scale. Its edges go into the view's `reference` layer, drawn with ISO 128-2 line type 05 (`drawing_document.PHANTOM_DASH`). It never occludes a delivered part and gets no balloon anchor. An operation whose `verify_joint` ends on existing equipment keeps its note and travel check, which names that end.
- `impact.py`: the product subject's `occurrences` domain holds `assembly.yaml` and each part's `make_buy`, so a placement-only edit stales `cad.product_exports@product`.
- cad-bom `bom.py` writes no row for existing equipment and never reports it in `missing_in_step`. Its children merge like a phantom's. `release.py` `check_bom` refuses a BOM row for it.
- `release.py`:
  - It ships `definition/product.step`.
  - With existing equipment in the record, it adds the in-context `assembly.step` under `context/`. It lists each interface with an existing end under `mates_with`.
  - It refuses a `product.step` that names existing equipment. It refuses a version with existing equipment and no `product.step`.
  - For a version without `product.step` and without existing equipment, it ships `definition/assembly.step`.
- cad-render `render_states` draws existing equipment with no `render` value as a ghost. The cad-viewer tree uses the same three groups. `parts hide|iso made|purchased|existing` acts on a whole group.
- Not yet covered: margins and stack-up against its range (bead benchhand-pcy, later PRs).

## Assembly sub-builds

`skills/cad-record/scripts/assembly_operations.py` owns assembly order. An operation with `sub_build` belongs to a bench sub-build with its own base, state and closure; an `install` operation puts it into the main build as one unit. A `hold` cure figure counts only with a `datasheet` source (`CURE_SOURCES`). `access_plan` takes obstacles from each operation's own build. cad-drawings `assembly_check.drawing_operations` leaves install operations out of the item steps. Records without these fields give the same results as before (`SubBuildTests` in `skills/cad-record/tests/test_assembly_operations.py`).

`skills/cad-record/scripts/quantities.py` holds the record's units table and the one quantity parser. `Quantity` and `IntentError` live there too. It needs only the standard library. `manufacturing_intent.py` loads it by path and re-exports every name. `assembly_operations.py` loads it directly, so `assembly_operations.py` and `cad-drawings` `assembly_check.py` stay free of PyYAML and jsonschema (`scripts/test_script_dependencies.py`). It runs on FreeCAD's Python 3.11, so `scripts/freecad_job_modules.txt` lists it.

## Tool and hand envelopes

The assembly access check never asks the user to model a tool or a hand.

- Owner: `skills/cad-record/scripts/tool_envelopes.py`, stdlib only. It classifies each `tools` or `tool_access` entry as hand, observe, bench, tool or note, and returns the shipped envelope of hand work and of sized common tools. `assembly_operations.py` `access_plan` turns the entries into `reaches` and `notes`. `skills/cad-motion/scripts/access.py` builds each envelope as coaxial cylinders in the operated part's frame, with the tip at the entry's `tool_access` `contact` point.
- Sources, copied into the tables with their table numbers:
  - hex keys: ISO 2936:2014 Table 1
  - Phillips PH1 to PH3: ISO 8764-1:2004 blade diameter and ISO 8764-2:2004 Table 1 series B length
  - wrench heads: ISO 3318:2016 Table 1
  - sockets: ISO 2725-1:2017 Tables 1 to 3
  - engineer's pliers: ISO 5746:2021 Table 1
  - hand: ISO 15534-3:2000 Table 1
- Values that no standard gives, each named in the envelope's sources:
  - Phillips handle diameter and length come from the FACOM ISORYL AP catalogue (supplier).
  - Wrench head thickness is an estimate, 0.4 s + 2 mm.
- Without a contact point, a tool that drives the part it installs or removes has its tip on the centre of the box face it arrives through: the face whose normal points most against the approach. A tool on a part that does not move (adjust, inspect), or an approach that meets two faces equally (45 degrees), needs the contact point, else the operation is unknown. The box centre is not where an off-centre feature is.
- An entry is read as one tool phrase: the whole entry names the tool and its size, and a socket takes the drive it names. Hex, Allen and Inbus keys are one tool. A size or a tool word inside other text makes no tool.
- A hold moves no part. `access_plan` plans its tools like any other step's. A tool that no shipped envelope or cited solid covers is advisory on a hold, and a hold whose tools are all advisory is `not_assessed` (`no_capability_profile`). An operation that moves no part and that nothing reaches carries its not-applicable reason (`AccessRequest.not_applicable`), which access puts in the row's coverage.
- A cited STEP solid in `tool_access` overrides the shipped envelope. A `tools` entry for the same tool as a `tool_access` entry is checked once, as that entry. Same tool means the same shipped designation (type, size and a socket's drive), else the same text. A name that matches no envelope is an instruction note in the outcome's uncertainty.
- Changes to `tool_envelopes.py` or `assembly_operations.py` change the access policy digest (`access.py` `policy_digest`), so the next run does not reuse a published packet.

## Feature flags

Environment flags follow `CAD_REQUIRE_FREECAD` (`skills/cad-flow/scripts/preflight.py`): only the string `1` turns one on.

| Flag | Default | Owner | Effect | Removal condition |
|---|---|---|---|---|
| `CAD_FEA_METALS_NEAR_SUPPORT` | off | package A (FEA) | A metal profile that fails the elongation rule is shown ductile by its form (wrought, sheet or rod). Only the shipped profiles `al_6061_t6_hydro`, `al_6082_t6_hydro`, `steel_dc01_salzgitter` and `stainless_304_outokumpu_core` count as metals, because the profile schema has no metal field. Isotropy is still required. Each strength verdict the flag decides carries `form stand-in, flag on` in `material.ductility.reason` and in the row's uncertainty. A material that passes the elongation rule is not affected. | Delete the flag by 2026-12-31 or when benchhand-y87 adds sourced elongation data to the metal profiles, whichever is first. |
| `CAD_CALIBRATION` | off | Package E | Printer calibration. Off is the release behaviour: a recorded FDM calibration is ignored (an info finding says so), and the generic guideline values govern. Under either setting, an applicable printer guideline settles a check in every service class: a calibration only tightens it. A printer check (FDM or resin) with no guideline value is not assessed (reason `no_guideline`): a minor finding that names the missing guideline, blocks no gate and is never a pass. A known failure in the same obligation still fails it. A printed stackup contributor is qualified by a guideline-backed deviation, or is not assessed when none covers it. A calibrated sliding clearance is not used. No remedy asks the user to calibrate; it says to print a test coupon on the target printer. On (`1`), recorded calibrations count and tighten the guideline values; a calibration whose size scope does not cover the feature is skipped before the guideline is used. The value is read once per job (cad-record `guidelines.calibration_enabled`), and its value (`1` or `0`) is always sealed into the form_check and stackup recipes and the check packets, so a bundle made under the other setting, or before the flag, is never reused. | Remove by 2026-12-31 or when calibration is released (dp2 and the non-FDM equivalents), whichever is first. |

## Commodity hardware sourcing

Commodity items (screws, nuts, washers, dowels, O-rings, heat-set inserts) are bought by designation, not by vendor part number.

- Schema: `purchase.designation` in `skills/cad-record/schemas/part.schema.json`. `kind: standard_designation` carries the standard, size, length (a number, or `not_applicable`), property class and optional finish. `kind: family_designation` covers items no ISO standard defines, such as a heat-set insert (thread, length, outside diameter, recommended hole). A purchase needs `identity` or `designation`. `reference_catalogue` (vendor, part number, URL) lists non-binding examples. Regenerate `docs/schemas.md` and `cad_api.py` with `scripts/gen_api.py` after a schema edit.
- Eligibility: `skills/cad-record/scripts/commodity_identity.py` decides which designations identify a commodity item: a known family (`heat_set_insert`) with its required fields, or a part standard with a size that fits it and a length. The size never embeds the length (`M4`, not `M4x20`). A feature standard such as ISO 965 is no part, a threaded standard needs a thread designation for its size (a property class such as 8.8 is no size), and `not_applicable` is only for nuts and washers. The module also holds the legacy text-designation parser (`designation_parses`, the standards-body and threaded-fastener lists), so `purchased.py` and the structured check share one matcher. A part whose `procurement.open` leaves an identity field open is not a commodity row.
- Checks: `skills/cad-design/scripts/process_checks/purchased.py`. An eligible designation row passes identity and availability with no supplier; an ineligible or unresolved one falls back to the manufacturer part number path. Availability means a named supplier, or commodity for an eligible designation only: a `commodity` claim on any other item stays unresolved. No lead time is asked for.
- Drawings: `skills/cad-drawings` reads the same helper (`drawing_intent.commodity_label`). A catalogue item bought by an eligible designation needs no manufacturer, part number or supplier on its drawing record or at release; its specification and source-control checks stay.
- BOM, validation and release packet: `skills/cad-bom/scripts/bom.py` and the record validator (`validate.py` `procurement_cells`) read the designation column through one lookup, `commodity_identity.designation_cell`, so a generated BOM validates. A supplied structured designation with an invalid field never stops the roll-up: `commodity_identity.designation_issues` returns each (field, reason), `bom.py` reports them as `invalid_designation` findings (exit 1) and keeps the row. The JSON that `bom.py` and `evidence.py digest` print is defined by `bom-summary.schema.json` and `evidence-digest.schema.json` (this repository holds JSON Schemas, not TypeSpec). `skills/cad-record/scripts/build_guide.py` merges eligible commodity rows into one buy row per designation, supplier and order code, with their PNs and summed quantity. A row bound to a named supplier, an order code or a named manufacturer stays on its own line and keeps that identity.
- Data: `skills/cad-fits/data/commodity.json` holds modelled dimensions with a public source URL per block; `skills/cad-fits/data/README.md` records what was checked. `skills/cad-fits/scripts/commodity.py` is the lookup and the one consumer. Values come from public pages, not controlled copies of the standards. A controlled copy governs.

## Schema API generator

`scripts/gen_api.py` is the only writer of two files. Never edit them by hand.

- `skills/cad-record/scripts/cad_api.py`: `TypedDict` classes and type aliases. It uses only the standard library and imports on Python 3.11, so FreeCAD job code can load it.
- `docs/schemas.md`: the schema reference.

The input is every `*.schema.json` file in two directories (`SCHEMA_DIRS`): `skills/cad-record/schemas` and `skills/cad-concept/assets/schemas`. The JSON Schemas stay the source. The run stops with `FAIL` on:

- a `$ref` that no schema declares as `$id`
- a duplicate `$id`
- a missing schema directory

`scripts/test_gen_api.py` fails when a schema file exists outside those two directories.

Both outputs open with a header that holds `schema-sha256` lines, one per schema, hashed over the exact file bytes. Any edit to a schema, including whitespace, makes the outputs stale until you regenerate.

Regenerate from the repository root with the command that both outputs print in their header:

```sh
python3 scripts/gen_api.py
```

`--check` regenerates in memory and writes nothing. It exits 1 when an output differs from the committed file or does not exist. The failure message names each stale file and each changed schema, and prints the regenerate command. CI runs it as the step "Generated typed API and schema reference" in `.github/workflows/ci.yml`:

```sh
uv run --no-project --python 3.11 python -B scripts/gen_api.py --check
```

Limits:

- An object that allows extra keys never becomes a `TypedDict`. Three cases:
  - With declared properties, it becomes `Mapping[str, Any]` (`OPEN_OBJECT`, `gen_api.py:347-349`). The type loses per-key precision. Type checkers skip the known key names and their value types. The table of known keys in `docs/schemas.md` is documentation only.
  - Without declared properties, it becomes `Mapping[str, V]`, where `V` comes from the `additionalProperties` schema (`inline_typed`, `gen_api.py:265-271`). `Assembly.profiles` is `Mapping[str, AssemblyProfile]`. `test_gen_api.py:443` expects `Mapping[str, str]` for a string value schema.
  - With unconstrained values, it becomes `Mapping[str, Any]` (same code).
- The generator does not emit PEP 728 `extra_items`, because mypy ignores it. Only objects closed with `additionalProperties: false` or `unevaluatedProperties: false` can be a `TypedDict`.
- A type cannot express the keywords in `NOTE_KEYS`, nor `if`/`then` and `dependentRequired`. The generator lists most of them in the docstring of `cad_api.py` and in `docs/schemas.md`, but those notes are not a complete list. For example, `Walker.collect` ignores a later refinement that holds only a bound on an existing property (`gen_api.py:324-325`). The `schema-sha256` headers still make `--check` fail on an edit that the renderer does not show.
- A `const` or `enum` with a float value cannot be a `Literal`. A `const` falls back to `Any`, and an `enum` falls back to the scalar type of its values.

Tests are in `scripts/test_gen_api.py`. They run in the light batch (`unittest discover -s scripts`).

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

Skill tests. Run each test file on its own with the test pins, from the directory that holds it. The "Tests" section of `README.md` loops over both locations:

```sh
for test in skills/*/test_*.py skills/*/tests/test_*.py; do
  [ -f "$test" ] || continue
  uv run --no-project --with-requirements harness/test-requirements.txt \
    python -B -m unittest discover -s "$(dirname "$test")" -p "$(basename "$test")"
done
```

CI lists the same two globs in `scripts/ci_shards.py` (`git ls-files "skills/*/test_*.py" "skills/*/tests/test_*.py"`) and runs one file at a time with `python -B scripts/ci_shards.py run <shard> <file>`.

CI splits the files into the shards `motion`, `motion-b`, `cad-model`, `freecad-gated` and `rest` (`scripts/ci_shards.py`, the `SHARDS` text in `ci.yml`). The files in the `freecad-gated` shard need FreeCAD 1.1.4 on `PATH` or `FREECADCMD`. Without it those tests skip, and `CAD_REQUIRE_FREECAD=1` turns the skip into a failure, as CI does. The `freecad-gated` shard also runs the mutation corpus rate check: `uv run --script skills/cad-design/tests/mfg_corpus/rates.py --check-baseline`. A pull request that changes only paths no skill test reads skips the shards (`scripts/docs_only.py`).

Agreement timing (not a CI gate): `uv run --script scripts/agreement_timing.py --runs 5` times `compare` on a seeded, generated mesh pair (160000 and 262144 triangles) with no FreeCAD; `skills/cad-model/tests/test_agreement_reference.py` pins the pair.

Git hooks are not defined in the repository (no hook files found). Never pass `--no-verify`; the guard test fails on it.

## Unverified items

- The profiling result, the proposed finer grid and the bead status behind benchhand-1tq.
- The plan, the reasons and the bead status behind benchhand-ci0.
