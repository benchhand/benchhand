# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0
"""A small PNG reader and camera projection for cad-render's pixel tests (8-bit, non-interlaced, no PLTE)."""

from __future__ import annotations

import math
import struct
import zlib

CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _paeth(left: int, above: int, corner: int) -> int:
    guess = left + above - corner
    return min((abs(guess - left), left), (abs(guess - above), above), (abs(guess - corner), corner))[1]


def _unfilter(kind: int, line: bytearray, previous: bytearray, channels: int) -> None:
    """Undo PNG filter `kind` on `line` in place, given the unfiltered line above it."""
    for i in range(len(line)):
        left = line[i - channels] if i >= channels else 0
        above = previous[i]
        corner = previous[i - channels] if i >= channels else 0
        predictor = (0, left, above, (left + above) // 2, _paeth(left, above, corner))[kind]
        line[i] = (line[i] + predictor) & 255


def _chunks(data: bytes) -> dict[bytes, bytes]:
    """The IHDR body and the joined IDAT bodies of a PNG."""
    found: dict[bytes, bytes] = {b"IHDR": b"", b"IDAT": b""}
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        if kind in found:
            found[kind] += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    return found


def decode(data: bytes) -> tuple[int, int, int, bytes]:
    """(width, height, channels, rows) of a PNG: `rows` holds height x width x channels bytes, unfiltered."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    chunks = _chunks(data)
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    if depth != 8 or colour not in CHANNELS or interlace:
        raise ValueError(f"unsupported PNG: depth {depth}, colour type {colour}, interlace {interlace}")
    channels = CHANNELS[colour]
    stride, raw = width * channels, zlib.decompress(chunks[b"IDAT"])
    rows, previous = bytearray(), bytearray(stride)
    for y in range(height):
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        _unfilter(raw[y * (stride + 1)], line, previous, channels)
        rows += line
        previous = line
    return width, height, channels, bytes(rows)


def column_of(camera: dict, point_mm: tuple[float, float, float], width: int) -> float:
    """The pixel column of an assembly point (mm) in a render.json camera record (pinhole, vertical fov)."""
    def sub(a, b): return [x - y for x, y in zip(a, b)]
    def dot(a, b): return sum(x * y for x, y in zip(a, b))
    def cross(a, b): return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    def unit(a): return [x / math.sqrt(dot(a, a)) for x in a]
    forward = unit(sub(camera["target_mm"], camera["position_mm"]))
    right = unit(cross(forward, camera["up"]))
    offset = sub(point_mm, camera["position_mm"])
    x_ndc = dot(offset, right) / dot(offset, forward) / math.tan(math.radians(camera["fov_deg"]) / 2) / camera["aspect"]
    return (x_ndc + 1) / 2 * width
