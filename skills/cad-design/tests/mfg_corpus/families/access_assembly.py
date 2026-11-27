# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-motion access.py: the geometric part-path and tool-reach check on the access fixture's built geometry.

Judged on the assembly.access obligations only (order is the assembly_operations_order family). Mutants and twins
reuse cad-motion's operation files. Each twin holds the same operations in an order or tool description that works.
"""

from typing import Any

from corpus import Family, Outcome, Pair
from support import assembly_verdict, reordered, socket_operations

ACCESS = "assembly.access@"
VALID = ("seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")  # screw driven before the bracket goes on


def _verdict(items: list[dict[str, Any]]) -> Outcome:
    return assembly_verdict(items, ACCESS)


def socket_blocked_by_bracket(mutant: bool) -> Outcome:
    items = socket_operations("blocked_socket.yaml")  # bracket mounted first: the socket cannot reach the screw
    return _verdict(items if mutant else reordered(items, *VALID))


def bearing_path_closed_by_cover(mutant: bool) -> Outcome:
    items = socket_operations("blocked_socket.yaml")  # the full set, so the screw and bracket are not in place from the start
    order = ("fit-cover", "seat-bearing", "fasten-cover", "mount-bracket") if mutant else VALID  # cover before the bearing
    return _verdict(reordered(items, *order))


def common_tool_too_wide(mutant: bool) -> Outcome:
    """The screw is driven through the bracket hole (r 3.5) with a hex key named in `tools`: its shipped ISO 2936 envelope
    decides. An 8 mm key (e max 9.09) collides with the bracket, a 3 mm key (e max 3.39) passes it. Nobody models the key."""
    items = socket_operations("blocked_socket.yaml")
    del items[3]["tool_access"]
    items[3]["tools"] = ["8 mm hex key" if mutant else "3 mm hex key"]
    return _verdict(items)


FAMILY = Family(
    id="access_assembly", check="cad-motion/scripts/access.py", owner="cad-motion",
    pairs=(
        Pair("socket_blocked", "bracket mounted before the screw is driven: the socket collides", socket_blocked_by_bracket),
        Pair("bearing_path_closed", "cover fitted over the seat before the bearing goes in", bearing_path_closed_by_cover),
        Pair("common_tool_too_wide", "a named hex key too wide for the bracket hole: its standard envelope collides",
             common_tool_too_wide),
    ))
