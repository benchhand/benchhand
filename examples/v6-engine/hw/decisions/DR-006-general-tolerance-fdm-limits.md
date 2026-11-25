---
status: accepted
date: 2026-11-25
decision-makers:
- the user
---

# General tolerance of the V6 engine drawings: FDM limits of 0.2 mm and 1 degree

## Context and Problem Statement

Every drawing states a general tolerance for the dimensions that carry no limits of their own. Q-013 asked which one. The parts are printed in ASA on an H2D class printer, so the ISO 2768 classes, which describe machined parts, do not fit.

## Considered Options

* ISO 2768-1 class m
* FDM limits of 0.2 mm linear and 1 degree angular

## Decision Outcome

Chosen option: "FDM limits of 0.2 mm linear and 1 degree angular", because a printed ASA part can be held to it and the lamp-arm example uses the same figure in its DR-004. Approval: the user answered Q-013 on 2026-11-25.

### Consequences

- This is an assumption, not a measured capability. No dimensional capability of the H2D is recorded and the figure carries no print evidence.
- Fits stay separate. The 3 g6 axles and the H7/g6 and H7/f7 pairs carry their own limits, so this policy does not cover them.
- Gauging a representative print would turn the assumption into a capability. Until then every dimension that depends on the general tolerance is as unproven as the capability itself.
