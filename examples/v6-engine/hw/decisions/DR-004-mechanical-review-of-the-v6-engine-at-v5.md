---
status: proposed
date: 2026-11-24
decision-makers:
- an independent read-only reviewer, session ses_df60a1580ffe9NEcW4amRF9JX5, at commit eb102786
---

# Mechanical review of the V6 engine at v5

## Context and Problem Statement

Independent reviewer: session ses_df60a1580ffe9NEcW4amRF9JX5 (read-only, separate model tier).
Reviewed commit eb102786006b40f365c2cf1b365fb5a965500663; v5 manifest SHA-256 4d163b5342c4e27e06f0fa0e73c4d8b509a845f49ed804e6f254655962701c77.

VERDICT: MAJOR — v5 cranktrain, REQ-001–006: motion, fits, assembly and rod strength.

Findings
- MECH-01 / clearance.nominal_contact — major, FAIL; REQ-003. The exact nominal separation of VSE-0001/VSE-0005 is 0.000 mm, with zero overlap. IF-007's positive diametral fit neither identifies nor excuses that contact. Locate the faces, then either provide clearance or specify an intended thrust interface. Recheck static clearance and the pair through motion from LAYOUT.
- MECH-02 / motion.coverage — major, UNKNOWN; REQ-001/003. 145 poses give zero collisions, with a minimum checked gap of 0.052 mm. 19 adjacent pairs are excluded, no pair minima are enforced, and 53,608 intervals are unproved for the six crank/rod pairs. Add running-pair criteria, explicitly check the adjacent pairs, refine the sweep and verify hand motion as built. Recheck the layout clearances and rerun ANALYSIS.
- MECH-03 / assembly.retention_access — major, UNKNOWN; REQ-001. Six clearance-fit pins are modeled as fixed, with no documented retention. The closed rod eyes and the one-piece crank and block have no installation sequence. Define retainers and demonstrate the insertion and service paths. Evaluate a split rod or a removable closure if needed. Recheck assembly access and play from LAYOUT.
- MECH-04 / stack.estimated_limits — major, UNKNOWN; REQ-001/003. The piston/bore arithmetic gives 0.30–0.65 mm diametral against an estimated 0.20 mm minimum, and the contributor limits are assumed. Gauge representative prints and rerun the as-manufactured stack at DETAIL.

Observed: REQ-002: the recorded bank axes are 60° apart. REQ-006: a 4 mm nose bore exists; drive engagement and torque transfer are untested.

Untested: REQ-004: the CSV reports 8.87 against ≥2, from v2, with an assumed 100 N, 40 MPa and one mesh. No solver bundle establishes contacts, convergence or the worst pose. Rerun ANALYSIS with sourced printed-grade properties and scoped loads. Wear, creep and life remain untested. REQ-005: see DFM.

Evidence: hw/requirements.yaml:2-37; .cad-work/check5.json:3456-3469; hw/checks/v5/cad/manifest.json:9; hw/motion/v5/engine-cycle/result.json (poses, pairs.excluded_adjacent, collision_pose_count, pair_clearance, worst_case, warnings, continuous.status); hw/motion/v5/engine-cycle/manifest.json (version_manifest_sha256, recipe.options); hw/interfaces.yaml:2-68; hw/assembly.yaml:130-148,220-228,364; hw/parts/VSE-0001/model.py:45-59; hw/parts/VSE-0004/model.py:27-40; hw/parts/VSE-0005/model.py:30-33,49-69; hw/analysis/cases/piston-bore/case.yaml:9-32; hw/analysis/cases/rod-compression/case.yaml:1-11; hw/verification.csv:2-7

## Considered Options

* Accept the findings and close them with design changes, prints and gauging
* Waive findings
* Decline the findings

## Decision Outcome

Chosen option: "Accept the findings and close them with design changes, prints and gauging", because Proposed disposition only. MECH-01 is a failing finding and gets a design change; the other findings stay open obligations..
