# Troubleshooting

## FreeCAD is not found

Setup and preflight check for FreeCAD 1.1.4 first. Install it as `skills/cad-drawings/SKILL.md` describes. Preflight looks for `freecadcmd` in this order: `FREECADCMD`, `PATH`, `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`, then `~/.local/bin/freecadcmd`. If `freecadcmd` is elsewhere, set `FREECADCMD` to its path. A wrong version stops setup the same way. Check with `freecadcmd --version`.

## CalculiX or gmsh is not found

FEA needs `ccx` and `gmsh`. Preflight looks through FreeCAD, then next to `freecadcmd`, then `PATH`. Set `CCX` and `GMSH` to the executables to override it. A set value is used as given.

## Preflight exit codes

| Command | Exit | Meaning |
|---|---|---|
| `preflight.py` | 0 | Every required check passed. |
| `preflight.py` | 1 | A required check failed. Read the report and fix it. |
| `preflight.py --status` | 0, 1 | The last warm-up passed or failed a required check. |
| `preflight.py --status` | 2 | No warm-up is recorded, or it died. Warm in the foreground. |
| `preflight.py --status` | 3 | The warm-up is still running. Run `--status` again, or add `--wait SECONDS`. |

## Chromium or Playwright fails

Run `preflight.py --warm`. It installs Chromium through Playwright and warms the render cache. The first kernel load has taken over 1100 s on a busy Mac. A render that cannot start Chromium in a sandbox needs permission to launch the browser process.

## Codex sandbox blocks a tool

`workspace-write` blocks loopback listeners and the Chromium and FreeCAD subprocesses. See "Codex CLI sandbox requirements" in the README.

## Kiro rejects `/cad-flow` or the profiles

- With `--no-interactive`, ask for the skill in prose. A bare `/cad-flow` parses as a CLI subcommand.
- The generated profiles use the V3 schema. Start with `kiro-cli chat --v3`. `--agent-engine v1` cannot load them.
- A warning about a missing KiroCrew `config/prompt.md` is not a failure.

## A build is refused

- Exit 2 from `cad_model.py run` or `stamp.py --verify` means no result. Read the stderr reason, fix it and run again. For `stamp.py`, do not commit.
- A missing or unapproved `flow.profile` blocks from SPECIFY on. Get the user's approval first: `record` writes the decision as `accepted` at once, so run it only after the user has answered. Show the user the questions:

  ```sh
  uv run --script "$CAD_SKILLS/cad-flow/scripts/flow_profile.py" questions
  ```

  `record` exits 2 and records nothing unless every one of its 16 options is answered or listed in `--accept-default`, FDM options included, whatever the parts are. `--hw` and `--decision` are required. This call answers the five general questions and accepts the default of the other eleven:

  ```sh
  uv run --script "$CAD_SKILLS/cad-flow/scripts/flow_profile.py" record --hw hw --decision DR-004 \
    --human-approval all --fea off --stack-up auto --critique full --results-in-git no \
    --accept-default orientation supports overhang-deg bridge-mm min-wall-mm min-feature-mm \
      warp cosmetic-faces print-in-place pip-clearance-xy pip-clearance-z
  ```

  Answer an option instead of accepting its default with its flag, such as `--supports free`. An option is never both answered and accepted. `record --help` lists the values. `flow_profile.py check --hw hw` reports what still blocks.
- A record that is not version 2 is refused.

## A script's output is not JSON

A producer prints a short summary on a terminal. Pass `--json` or set `CAD_OUTPUT=json` when a script parses stdout.

## `--tier-model` fails

It needs PyYAML. Run the installer under `uv run --no-project --with pyyaml`.
