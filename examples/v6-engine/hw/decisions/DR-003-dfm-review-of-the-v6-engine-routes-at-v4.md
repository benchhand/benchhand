---
status: proposed
date: 2026-11-23
decision-makers:
- the lead agent, following cad-review-dfm; the user has not yet dispositioned the findings
---

# DFM review of the V6 engine routes at v4

## Context and Problem Statement

Read-only review of the five FDM routes (print on the H2D, ASA) against form_check F2 on v4 (.cad-work/form4.json) and the record. Verdict: MAJOR, because the print orientation, the support plan and the warp calibration of every part are UNKNOWN and REQ-005 is mandatory.

Findings:
- D1 major unknown, all five parts: print orientation is `proposed`, not approved (process.fdm.orientation_unapproved). Layer direction, first-layer contact and supports are not judged in the pose that will print. Remedy: approve an orientation per part with a decision.
- D2 major unknown, all five parts: no support plan (process.fdm.support_unplanned). The V block is the hard case: its bank bores lean 30 degrees from the build axis in the model pose, and one pose cannot stand both banks upright. Alternatives: print the block in two halves, or accept supports in the bores and ream them.
- D3 minor unknown, all five parts: warp is uncalibrated (process.fdm.warp_uncalibrated). No calibration of the H2D with ASA is recorded.
- D4 minor: the 6 mm wrist pins are printed tubes in printed piston bores. A sourced 6 mm steel dowel (ISO 2338 h8) would give a known fit and strength; the 6 H7/g6 fit by cad-fits is a design fit, not demonstrated FDM capability.
- D5 info, resolved: form_check on v3 failed process.fdm.wall on the crankcase (1.001 mm) and the piston (0.994 mm) against the 1.2 mm guideline; v4 passes after the window diameter and the piston cavity changed.

Untested: tool access and fastener checks (no fasteners in this scope), support removal damage.

## Considered Options

* Accept the findings and approve orientations and a support plan
* Change the routes
* Decline the findings

## Decision Outcome

Chosen option: "Accept the findings and approve orientations and a support plan", because Proposed disposition only. The user owns the orientation approvals; nothing here converts UNKNOWN to PASS..
