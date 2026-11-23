# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0003 Wrist pin: CadQuery 2.8, part_local frame, placed in six occurrences. Axis along Y, centred on the origin."""

import cadquery as cq

KERNEL = "cadquery"
PARAMS = {"d": 5.996, "length": 20.0, "bore_d": 3.0}  # 6 g6 upper deviation -0.004; the piston bore is 6 H7


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    pin = cq.Workplane("XZ").circle(p["d"] / 2.0).circle(p["bore_d"] / 2.0).extrude(p["length"] / 2.0, both=True)  # tube: a printed solid pin is weak and the joint needs a bore
    return pin.faces("|Y").chamfer(0.3).rotate((0, 0, 0), (0, 0, 1), 90).val()  # pin axis X
