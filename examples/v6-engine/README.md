# V6 engine example

This example takes a 3D-printable desktop V6 engine through the Benchhand flow. The model has a 60 degree V block, a crankshaft, and six rods, pistons and wrist pins. The process is FDM on a Bambu H2D class printer, in ASA. The [root README](../../README.md#showcase) tells the story with the tool output. This file records what the example models and what stays open.

## Scope

- This example models one rod, one piston and one wrist pin definition, each placed six times (`VSE-0004`, `VSE-0002`, `VSE-0003`).
- The crankcase (`VSE-0001`) and the crankshaft (`VSE-0005`) complete the crank train.
- The crank nose has a 4 mm bore for a hand crank pin or a motor coupling (REQ-006). The example models no motor and no motor mount, so it needs no customer-supplied part.
- The use-case unknowns (service class, loads, environment, life) stay recorded as `unknown` with open questions.
- The part numbers use the prefix `VSE`, because the PN schema allows letters only.

## State

- The record is `hw/`, and `validate.py` passes.
- The human gates, the print orientations and the gauged fits are open.
- DR-003 and DR-004 hold the DFM and mechanical reviews with status `proposed`. Their findings stay open obligations.
- No release is sealed. The `release.py` dry run refuses: no accepted release decision, nothing frozen, and manufacturing is not a complete pass.
- Generated results (versions, checks, motion, renders, drawings) stay out of git, as DR-001 records. Rebuild them with the commands in `docs/walkthrough.md`.

## Known limits of the tools

- The 720 degree sweep skips pairs that a joint connects, so only the commit-time check found the piston and rod overlap.
- The static FEA case is exploratory. It has no retained solver bundle, so REQ-004 reads `not_assessed`.
