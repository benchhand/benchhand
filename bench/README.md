# Agent-throughput benchmark

This benchmark measures how the cad-flow lead and its subagents spend turns, tokens, money and time when they take a case from its frozen L1 LAYOUT through the L2 PARTS gate. A scripted user answers every question, and the harness then judges the result.

**Local only.** `run.py` drives a paid model. Run it on a developer machine, never in CI or on a shared host. It exits with an error when `CI` or `GITHUB_ACTIONS` is set.

## Layout

| Path | Contents |
|---|---|
| `cases/<case>/brief.md` | The user brief the lead receives. |
| `cases/<case>/answers.yaml` | Scripted answers, matched by intent, and the case's GROUND facts (`slots`). |
| `cases/<case>/requirements.yaml` | Must-have acceptance requirements for the judge, and whether the hard checks include the sweep. |
| `cases/<case>/fixture/hw/` | The frozen L1 record and its built L1 version. |
| `cases/_common/answers.yaml` | Gate approvals, workflow answers and the shared GROUND question patterns. |
| `config.json` | Producer and judge models, the price table and its source, caps and the throwaway OpenCode config. |
| `run.py` | Runs one case once. |
| `report.py` | Statistics over the run records. |

## Run

`run.py` needs a checkout of the `bench/results` branch for its records:

```sh
git worktree add ../cad-agent-bench-results bench/results
uv run --script bench/run.py --case wall-bracket --arm baseline --results ../cad-agent-bench-results
```

Each run does these steps:

1. It copies the fixture into a scratch git project under the OpenCode temp directory.
2. It installs this checkout's OpenCode harness into a throwaway OpenCode home with `harness/install.py --harness opencode --user`. Your own `~/.config/opencode` is never read.
3. It drives the lead with `opencode run --format json --auto --archived` and continues the same session.

The run stops when the PARTS gate is answered on a built L2 version. It also stops at the turn, time or spend cap in `config.json`, and that counts as a failure. The harness then runs the hard checks: `validate.py`, `cad_model check`, `form_check` at F2 with its process packet, and the sweep where `requirements.yaml` asks for it. A judge model from another vendor then scores the rubric from the record, the check output and the renders.

### Isolation check

Before the first model call, the harness starts `opencode serve` in the run's environment and asks it what it loaded:

- `config.get`: the config sources.
- `skill.list`: every skill, with its path.
- `agent.list` and `command.list`: the agent and command names.

The run continues only when every config source and skill path lies inside the throwaway home or the scratch project, or is a built-in `skill://` skill. Every agent and command must be a built-in or one the harness installed, and every installed skill must be listed. The cache directory OpenCode reports (`opencode debug paths cache`) and `XDG_CACHE_HOME` must both lie inside the run home. Otherwise the run records `isolation_failed`, spends nothing and exits 4. OpenCode loads skill sources one at a time, so the check waits until the skill list holds still for three polls. Without `OPENCODE_TEST_HOME`, the same check reports the skills in `~/.agents/skills`.

Each run has its own config, data, state and cache directories in the throwaway home. Config, data and state entries from the user's own directories are linked in, except `opencode` and `cad-viewer`.

From commit 7564291 on, the cache is an allowlist. The private cache links only these package-download caches: `uv`, `pip`, `ms-playwright` and `cad-concept`. `cad-concept` holds the three.js modules that cad-render's import map pins by SRI digest, and preflight verifies each digest. Every other cache starts empty in each run, so no state carries from one run into the next: fontconfig, FreeCAD, OCC, cad-flow's warm-up record and the rest. The isolation check fails the run when a private-cache link points anywhere but its allowlisted cache, or when an entry is not a real directory inside the run home. It also fails when a package-cache variable (`UV_CACHE_DIR`, `PIP_CACHE_DIR`, `PLAYWRIGHT_BROWSERS_PATH`) points outside the run home at a cache that is not allowlisted. Runs before 7564291 shared every cache except `opencode`, `cad-flow` and `cad-viewer` with the user, non-download caches included.

The lead's viewer sessions and `preflight.py --warm` record therefore land in the run home. `UV_CACHE_DIR` stays on the user's uv cache: it holds packages, not user state, and a private copy would download every wheel again in each run. At the end of a run, the harness stops each viewer and each running warm-up through the pid that the run's private records name. Nothing else is signalled unless the run itself started its process group.

### Failed tool calls

The sanitized session exports drop every tool output. So, before the scratch directory goes, the harness reads the unsanitized exports of the lead and every subagent and writes `tool-errors.jsonl` next to `record.json`. That file has one line for each shell call that ran a repository record or check tool and exited non-zero. The covered tools are `stamp.py`, `validate.py`, `cad_model.py`, `scratch.py`, `form_check.py`, `evidence.py`, `flow.py` and any `cad-*/scripts/*.py` run through `run.py`.

Each line holds:

- the command as argv, with paths made relative to the scratch project and `$CAD_SKILLS`;
- the exit code;
- the last 4 kB of output.

OpenCode's shell tool reports stdout and stderr merged in one text, so the tail covers both (`"streams": "merged"`). When OpenCode truncated a long output, the harness reads the whole of it from the file OpenCode saved in the run's own home. Values that look like keys, tokens or passwords are masked; sha256 digests are kept.

### Questions the script cannot answer

An ask that matches no intent gets the default answer, which records a bounded assumption and continues. Markdown headings are never asks, though a heading that names a gate gives an approval request below it its meaning. A "Decision for you:" or "Please choose:" label followed by option bullets is one ask, and its options are recorded with it. The record counts these asks in `metrics.questions_unmatched` and lists each one in `unmatched_questions`, with its turn. When the lead presents a gate and asks for approval in the same message as an unmatched question, the run fails as `fail:unmatched-at-gate`, and the harness sends no reply. A gate prompt is a message with an ask that matches a gate intent, or one that asks for approval while it names a gate or a freeze.

### Gate prompts and the record's stage

From commit a0df35c on, a gate counts only when it fits the record's state at the end of the lead's turn:

- INTENT, only while `flow.stage` is `ground` or `intent`;
- VARIANT, only while `flow.stage` is `concept`;
- the LAYOUT freeze, only once `hw/versions/` holds a version built at `layout`;
- the PARTS gate, only once `hw/versions/` holds a version built at `parts` (from commit 7564291 on).

Both gates are bound to a built version, not to `flow.stage`, for one reason. The provisional gate moves `flow.stage` to the next stage before the human gate is shown: to `parts` before the LAYOUT freeze, and to `detail` before the PARTS gate. A literal check on the stage would therefore reject the real prompt.

At another stage, the harness matches the ask to the other intents instead of that gate. A GROUND reply that names a later gate is then no gate prompt, so its open questions can no longer fail the run as `fail:unmatched-at-gate`. Commit-timing, flow-profile and SPECIFY approval questions are not stage-bound. Each dialogue entry records `stage`, `has_l1` and `has_l2`.

This changes what `metrics.gates_answered`, `metrics.questions_unmatched_at_gate` and the `fail:unmatched-at-gate` outcome count. Results from runs before a0df35c use the old definition, so compare gate metrics only between runs on the same definition. The first run on the new definition is small-mechanism-fixture-build-20261118T003143-ebc5, at 224f5f0.

Each run commits its record to `bench/results`, appends `spend.jsonl` and pushes. It then deletes its scratch directory. `--mode fixture` builds a fixture instead. It starts from an empty project, or from `--seed-hw`, stops at the LAYOUT freeze, and copies the frozen `hw/` into the case.

Each run also owns `cad-plan-c-bench/<run-id>/` in the OpenCode temp directory. It writes `pid` there at start and `done`, holding its exit code, at the end. It refuses a run id whose `pid` or `done` already exists. A launcher creates that directory first, passes `--run-id` and sends the output to `log` inside it, so a watcher reads only its own run's files:

```sh
id=wall-bracket-baseline-$(date +%Y%m%dT%H%M%S)-$(openssl rand -hex 2)
dir=/private/var/folders/8d/02tkxcq55qzfyj7_z_ktft1r0000gq/T/opencode/cad-plan-c-bench/$id
mkdir -p "$dir" && uv run --script bench/run.py --case wall-bracket --arm baseline --run-id "$id" \
  --results ../cad-agent-bench-results > "$dir/log" 2>&1
```

## Report

```sh
uv run --script bench/report.py summary ../cad-agent-bench-results
uv run --script bench/report.py compare ../cad-agent-bench-results --a baseline --b candidate
uv run --script bench/report.py size ../cad-agent-bench-results --budget 600
uv run --script bench/report.py spend ../cad-agent-bench-results
```

`summary` gives p50, p90 and CV per metric, per case and overall, plus the acceptance rate and accepted designs per dollar. `compare` gives per-case log ratios between two arms with bootstrap confidence intervals. `size` gives the repeats per case and arm needed to detect a 20% change at 80% power, α 0.05, and what they cost.

## Tests

```sh
uv run --no-project --with pyyaml==6.0.3 python -B -m unittest discover -s bench/tests -p 'test_*.py'
```

The tests replay recorded OpenCode session exports and call no model.
