---
status: proposed
date: 2026-11-23
decision-makers:
- the lead agent, following cad-review-mech; the user has not yet dispositioned the findings
---

# Mechanical review of the V6 engine at v4

## Context and Problem Statement

Read-only review of REQ-001 to REQ-006 against the version-bound results of v4. Verdict: MAJOR, because the piston-to-bore stack is UNKNOWN for a binding consequence.

Findings:
- M1 major unknown: stack STK-001 (piston in bore) gives 0.30 to 0.65 mm against 0.2 mm required, but its contributor limits are estimates, not calibrated capability, so the tool reports the requirement as not established. Remedy: print a test coupon on the target printer and record its measured diameters.
- M2 minor: the 720 degree sweep passes (0 collisions, smallest clearance 0.052 mm, rod 5 to pin 5), but the between-sample check is incomplete with 864 unproved intervals. Accept them with a decision or rerun with a finer grid.
- M3 minor: the rod FEA (100 N compressive, peak 4.51 MPa, safety factor 8.87) rests on an assumed load and an assumed ASA yield of 40 MPa, one mesh, no sourced grade. It is exploratory, not service evidence, and a static solve does not cover buckling of the 7 mm web, gas pressure pulses, inertia or fatigue.
- M4 minor: the fits of REQ-003 are design limits (6 H7/g6 clearance 4 to 24 um, 14 H7/f7 clearance 16 to 52 um). The rod bores (6.1 and 12.2 mm) are running clearances outside ISO 286 and are recorded as such.
- M5 info: REQ-002 (60 degree V) holds by inspection of the bank axes at plus and minus 30 degrees in assembly.yaml; REQ-006 holds by inspection of the 4 mm coupling bore in the crank nose.

Untested: gas load, inertia, wear, creep and life of printed running surfaces.

## Considered Options

* Accept the findings and close M1 with a coupon
* Waive M1
* Decline the findings

## Decision Outcome

Chosen option: "Accept the findings and close M1 with a coupon", because Proposed disposition only. No disposition converts UNKNOWN or an exploratory result to PASS..
