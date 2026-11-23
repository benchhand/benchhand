---
status: accepted
date: 2026-11-23
decision-makers:
- the brief
---

# Print the engine parts in ASA on a Bambu Lab H2D class printer

## Context and Problem Statement

Every custom part of the V6 engine is an FDM part. The brief names ASA (or PLA) on a Bambu H2D class printer. Without a named capability, every printability check stays unknown.

## Considered Options

* Leave the print operations without a capability
* Print every custom part in ASA on the H2D profile

## Decision Outcome

Chosen option: "Print every custom part in ASA on the H2D profile", because the brief names the printer class and the material family. PLA stays a fallback. No filament grade is chosen, so the material profile holds no sourced properties.

### Consequences

- The capability-unknown findings of the print operations are gone. The checks ask for geometry and an approved print orientation.
- Strength figures stay unknown until a grade and its conditions are recorded. Any FEA result states that.
