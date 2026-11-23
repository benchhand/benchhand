---
status: accepted
date: 2026-11-23
decision-makers:
- the user
---

# Flow profile

## Context and Problem Statement

The user answered the project-start questions (cad-flow `flow_profile.py questions`) at INTENT. `product.yaml` `flow.profile` records the answers. The lead showed the user each question with its default: `user` marks an answer the user gave, `asked-accepted-default` a default the user accepted, and `stated` the motion sweep, which runs once a joint moves and is never asked.

## Considered Options

* Record the user's answers as flow.profile

## Decision Outcome

Chosen option: "Record the user's answers as flow.profile", because the user answered each project-start question or accepted its default. Approval: confirmed by the user on 2026-11-23.

## More Information

| Setting | Answer | Asked |
|---|---|---|
| human_approval | key | user |
| fea.enabled | true | user |
| fea.mode | accurate | user |
| stack_up | on | user |
| critique | full | user |
| motion | auto | stated |
| storage_results_in_git | false | user |
| processes.fdm.orientation | propose | user |
| processes.fdm.supports | allowed | user |
| processes.fdm.overhang_deg | calibrated | user |
| processes.fdm.bridge_mm | calibrated | user |
| processes.fdm.min_wall_mm | calibrated | user |
| processes.fdm.min_feature_mm | calibrated | user |
| processes.fdm.warp | calibrated | user |
| processes.fdm.cosmetic_faces | false | user |
| processes.fdm.print_in_place | false | user |
| processes.fdm.pip_clearance_mm.xy | calibrated | user |
| processes.fdm.pip_clearance_mm.z | calibrated | user |
