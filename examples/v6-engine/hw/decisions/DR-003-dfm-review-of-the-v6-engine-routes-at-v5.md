---
status: proposed
date: 2026-11-24
decision-makers:
- an independent read-only reviewer, session ses_df60a1580ffe9NEcW4amRF9JX5, at commit eb102786
---

# DFM review of the V6 engine routes at v5

## Context and Problem Statement

Independent reviewer: session ses_df60a1580ffe9NEcW4amRF9JX5 (read-only, separate model tier).
Reviewed commit eb102786006b40f365c2cf1b365fb5a965500663; v5 manifest SHA-256 4d163b5342c4e27e06f0fa0e73c4d8b509a845f49ed804e6f254655962701c77.

VERDICT: MAJOR — VSE-0001–0005, ASA FDM on Bambu H2D; print, deburr and assembly access.

Make/buy: five made, single-solid definitions; no bought items. The largest proposed plan dimension is 134 mm, within REQ-005's 256 mm bed criterion before supports or brim.

Findings
- DFM-01 / process.fdm.support_unplanned — major, UNKNOWN; REQ-005. All poses are proposed. Support contact and removal are unproved for the block cavity and seats, the piston slot, the wrist-pin lumen, the rod eyes and the crank journals. Approve the poses and show first-layer contact, bridges and reachable removal. Specify finishing allowance and inspection wherever support touches a running surface. Recheck form at PARTS on the new geometry and route.
- DFM-02 / fit.capability_unproved — major, UNKNOWN; REQ-003. The 4–24 µm and 16–52 µm ISO clearances are design limits. The H2D profile has no dimensional capability evidence, and deburr is the only finish. Gauge representative ASA prints, compare a finished bore with a sourced pin or bearing, then recheck the finished stacks at DETAIL.

Assembly: the installation order and access are untested, and the operations are unknown. No fastener operation is declared.

Checks not assessed: v5 skipped form, and v4's 1.594 mm piston-wall result predates the slot change, so rerun the regional walls and holes. Warp, support-removal damage, and material suitability for wear, heat or creep are untested: there is no representative print, selected grade or service condition.

Evidence: hw/bom.csv:2-6; hw/requirements.yaml:14-31; .cad-work/check5.json (payload.checks.solid_structure, payload.checks.manufacturing.parts.*.checks.placement); hw/versions/v5/manifest.json (parts.*.params, record, manufacturing); hw/interfaces.yaml:2-68; hw/manufacturing/capabilities.yaml:4-6,124-132; hw/manufacturing/materials.yaml:3-19; hw/product.yaml:55-79; hw/assembly.yaml:364; .cad-work/ev6.json:7-8,23-38; .cad-work/form4.json:363,1790

## Considered Options

* Accept the findings and close them with prints, gauging and approved orientations
* Change the routes
* Decline the findings

## Decision Outcome

Chosen option: "Accept the findings and close them with prints, gauging and approved orientations", because Proposed disposition only. The findings stay open obligations; no disposition converts UNKNOWN to PASS. Closing them needs prints, gauging or a decision by the user..
