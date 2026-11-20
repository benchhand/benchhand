# AGENTS.md

Benchhand is a set of agent skills and agent profiles for mechanical CAD. The skills guide a product from concept to a verified STEP AP242 release. Skills live under `skills/`, the profile sources under `agents/`, and the harness generator and installer under `harness/`.

Read [docs/engineering.md](docs/engineering.md) before any work: it holds the stack, pins, commands and approved departures. Read [docs/architecture.md](docs/architecture.md) for the system shape. [CONTRIBUTING.md](CONTRIBUTING.md) covers the contributor license agreement; this file does not repeat it.

## Rules for this repository

- Work test-first: write the failing test, then the change. Test commands are in `docs/engineering.md`.
- Every source file carries an SPDX header. `REUSE.toml` covers data and documents, so Markdown takes none. Run `uvx --from 'reuse[charset-normalizer]==6.3.0' reuse lint` before you push.
- Never edit the generated profiles under `harness/claude`, `harness/codex`, `harness/opencode` or `harness/kiro`. Change `agents/` or `harness/generate.py`, then run `python3 harness/generate.py --check`; CI runs the same check.
- Never pass `--no-verify` to git. `scripts/test_no_verify_guard.py` fails on it.
- Code that `freecadcmd` runs must import on Python 3.11 with only PyYAML and jsonschema. `scripts/freecad_job_modules.txt` lists it and `scripts/import_check.py --manifest` checks it. All other scripts keep a `requires-python` floor of 3.12 (`scripts/test_script_floors.py`).
- Shipped skill code never imports `harness/` or `scripts/`.
