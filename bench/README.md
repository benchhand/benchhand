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

Each run commits its record to `bench/results`, appends `spend.jsonl` and pushes. It then deletes its scratch directory. `--mode fixture` builds a fixture instead. It starts from an empty project, or from `--seed-hw`, stops at the LAYOUT freeze, and copies the frozen `hw/` into the case.

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
