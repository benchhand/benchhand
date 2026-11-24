---
status: accepted
date: 2026-11-24
decision-makers:
- the lead (reversible)
---

# Drive both camshafts with one serpentine GT2 belt in one plane

## Context and Problem Statement

The banks are staggered by 10.5 mm along the crank (head-1 at x = -5.25, head-2 at x = +5.25). With one camshaft design for both heads, the cam pulleys sit at x = -79.25 and x = -68.75: two belt planes. The 8 mm crank pulley on the 14 mm nose (x -74 to -60) can serve only one of them while it keeps 2 mm clear of the crankcase end at x = -60.

## Considered Options

* A two-track crank pulley on a longer crank nose
* Two crank pulleys and two belts
* Camshaft stubs of different length per bank, so both cam pulleys share the crank pulley's plane, with one serpentine belt

## Decision Outcome

Chosen option: "Camshaft stubs of different length per bank", the usual production V6 layout. The belt plane is x = -68.75. The bank B camshaft (VSE-0007, 156 mm) already puts its pulley there. The bank A camshaft (VSE-0011, 135 mm) moves its stub inboard by 10.5 mm. The crank pulley VSE-0008 (20T) sits at x = -68.75 on the current nose: faces at -72.75 and -64.75, 4.75 mm clear of the crankcase.

Belt path in the belt plane (y, z from the crank axis): crank pulley (0, 0) r 6.37; cam pulleys (±45, 77.94) r 12.73; idler (0, 70) r 5, back side; tensioner at z = 30, back side, travel y -30 to -6. The pitch length runs from 411.2 mm to 424.3 mm over the travel. The stock 420 mm GT2 closed loop (210 teeth) sits at y = -10.7.

### Consequences

- Two camshaft part numbers instead of one.
- One belt, one tensioner and one idler instead of two belts.
- Reversible: if the bank A stub proves too long or too weak, fall back to two crank pulleys.
