"""Extrema over the edges of an OCP shape. They bound a linear function (the arm of a contact patch) and the distance
to a line (the reach of a moving part). Lines and circle arcs give exact values, any other edge a bound.

A linear function, and a convex one like a distance, takes its extreme over a convex set at an extreme point of the
set. So the edges of a face bound a linear function over the face, and the edges of a ruled solid bound a distance
over the solid. Per edge:

- a line: its two ends, exact.
- a circle or arc: the linear function in closed form, at the two ends and at the angle atan2(b, a) and its
  opposite when they fall inside the arc. The distance comes from the roots of its derivative, bracketed on a fine
  grid and bisected, accurate to rounding.
- any other curve: the poles of its NURBS form, which enclose the curve. The result is a safe bound: at least the true
  maximum and at most the true minimum.

A face that is no plane, cylinder or cone can bulge past its edges, so the distance bound takes the corners of its
bounding box instead.
"""

from __future__ import annotations

import math
from typing import Any

Vec = tuple[float, float, float]
GRID = 2048  # grid steps over an arc, to bracket the roots of the derivative of its squared distance
BISECTIONS = 80
FULL_TURN = 2.0 * math.pi


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _xyz(point: Any) -> Vec:
    return (point.X(), point.Y(), point.Z())


def _explore(shape: Any, kind: Any, cast_name: str) -> list[Any]:
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    cast = getattr(TopoDS, cast_name + "_s", None) or getattr(TopoDS, cast_name)
    found, explorer = [], TopExp_Explorer(shape, kind)
    while explorer.More():
        found.append(cast(explorer.Current()))
        explorer.Next()
    return found


def edges(shape: Any) -> list[Any]:
    from OCP.TopAbs import TopAbs_EDGE

    return _explore(shape, TopAbs_EDGE, "Edge")


def solids(shape: Any) -> list[Any]:
    from OCP.TopAbs import TopAbs_SOLID

    return _explore(shape, TopAbs_SOLID, "Solid")


def faces(shape: Any) -> list[Any]:
    from OCP.TopAbs import TopAbs_FACE

    return _explore(shape, TopAbs_FACE, "Face")


class Arc:
    """A circle arc: center, the two unit vectors of its plane, radius and parameter range (radians)."""

    def __init__(self, center: Vec, u: Vec, v: Vec, radius: float, first: float, last: float) -> None:
        self.center, self.u, self.v, self.radius, self.first, self.last = center, u, v, radius, first, last

    @property
    def span(self) -> float:
        return min(self.last - self.first, FULL_TURN)

    def contains(self, angle: float) -> bool:
        return self.span >= FULL_TURN - 1e-12 or (angle - self.first) % FULL_TURN <= self.span + 1e-12


def classify(edge: Any) -> tuple[str, Any]:
    """("line", (p, q)), ("arc", Arc) or ("poles", [points]) for one edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    kind = curve.GetType()
    if kind == GeomAbs_Line:
        return "line", (_xyz(curve.Value(first)), _xyz(curve.Value(last)))
    if kind == GeomAbs_Circle:
        circle = curve.Circle()
        return "arc", Arc(_xyz(circle.Location()), _xyz(circle.XAxis().Direction()), _xyz(circle.YAxis().Direction()),
                          circle.Radius(), first, last)
    return "poles", _poles(edge, curve, first, last)


def _poles(edge: Any, curve: Any, first: float, last: float) -> list[Vec]:
    """The poles of the NURBS form of `edge`, which enclose it. An edge that fails to convert gets 256 samples."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    try:
        converted = edges(BRepBuilderAPI_NurbsConvert(edge, True).Shape())[0]
        spline = BRepAdaptor_Curve(converted).BSpline()
        return [_xyz(spline.Pole(i)) for i in range(1, spline.NbPoles() + 1)]
    except Exception:  # noqa: BLE001 - a kernel failure gives samples
        return [_xyz(curve.Value(first + (last - first) * i / 256)) for i in range(257)]


def linear_range(shape: Any, gradient: Vec, origin: Vec) -> tuple[float, float]:
    """(min, max) of gradient . (p - origin) over the edges of `shape`: exact for lines and arcs, a bound beyond."""
    values: list[float] = []
    for edge in edges(shape):
        kind, data = classify(edge)
        if kind == "line":
            values += [_dot(gradient, _sub(p, origin)) for p in data]
        elif kind == "poles":
            values += [_dot(gradient, _sub(p, origin)) for p in data]
        else:
            values += _arc_linear(data, gradient, origin)
    return min(values), max(values)


def sampled_range(shape: Any, gradient: Vec, origin: Vec, samples: int = 256) -> tuple[float, float]:
    """(min, max) of gradient . (p - origin) over the vertices of `shape` and `samples` points on each curved edge.
    An estimate that is tight where `linear_range` is only a bound. It measures a sliver of overlap and decides nothing."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    values: list[float] = []
    for edge in edges(shape):
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        steps = 1 if classify(edge)[0] == "line" else samples
        values += [_dot(gradient, _sub(_xyz(curve.Value(first + (last - first) * i / steps)), origin)) for i in range(steps + 1)]
    return min(values), max(values)


def _arc_linear(arc: Arc, gradient: Vec, origin: Vec) -> list[float]:
    """f at an arc's ends and at the angles where f is largest and smallest on the whole circle, when they lie on the arc."""
    base = _dot(gradient, _sub(arc.center, origin))
    a, b = arc.radius * _dot(gradient, arc.u), arc.radius * _dot(gradient, arc.v)
    angles = [arc.first, arc.first + arc.span]
    peak = math.atan2(b, a)
    angles += [angle for angle in (peak, peak + math.pi) if arc.contains(angle)]
    return [base + a * math.cos(angle) + b * math.sin(angle) for angle in angles]


def reach(shape: Any, origin: Vec, axis: Vec) -> float:
    """An upper bound of the largest distance from the line through `origin` along unit `axis` to any point of `shape`."""
    best = 0.0
    for edge in edges(shape):
        kind, data = classify(edge)
        points = list(data) if kind in ("line", "poles") else None
        best = max(best, max(distance(p, origin, axis) for p in points) if points is not None else _arc_reach(data, origin, axis))
    for face in faces(shape):
        if not _ruled(face):
            best = max([best, *(distance(corner, origin, axis) for corner in _box_corners(face))])
    return best


def distance(point: Vec, origin: Vec, axis: Vec) -> float:
    d = _sub(point, origin)
    along = _dot(d, axis)
    return math.sqrt(max(0.0, _dot(d, d) - along * along))


def _arc_reach(arc: Arc, origin: Vec, axis: Vec) -> float:
    """The largest distance from the axis over an arc, by the roots of the derivative of its square."""
    w0 = _perpendicular(_sub(arc.center, origin), axis)
    w1 = tuple(arc.radius * x for x in _perpendicular(arc.u, axis))
    w2 = tuple(arc.radius * x for x in _perpendicular(arc.v, axis))

    def vector(t: float) -> Vec:
        c, s = math.cos(t), math.sin(t)
        return tuple(w0[i] + w1[i] * c + w2[i] * s for i in range(3))  # type: ignore[return-value]

    def slope(t: float) -> float:  # half the derivative of the squared distance
        c, s = math.cos(t), math.sin(t)
        return _dot(vector(t), tuple(-w1[i] * s + w2[i] * c for i in range(3)))  # type: ignore[arg-type]

    start, span = arc.first, arc.span
    grid = [start + span * i / GRID for i in range(GRID + 1)]
    candidates = list(grid)
    for left, right in zip(grid, grid[1:]):
        low, high = slope(left), slope(right)
        if low == 0.0 or low * high < 0.0:
            a, b = left, right
            for _ in range(BISECTIONS):
                mid = (a + b) / 2.0
                a, b = (mid, b) if slope(a) * slope(mid) > 0.0 else (a, mid)
            candidates.append((a + b) / 2.0)
    return max(math.sqrt(_dot(vector(t), vector(t))) for t in candidates)


def _perpendicular(vector: Vec, axis: Vec) -> Vec:
    along = _dot(vector, axis)
    return (vector[0] - along * axis[0], vector[1] - along * axis[1], vector[2] - along * axis[2])


def _ruled(face: Any) -> bool:
    """Whether the face lies in the hull of its edges: a plane, a cylinder or a cone."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane

    return BRepAdaptor_Surface(face).GetType() in (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone)


def _box_corners(face: Any) -> list[Vec]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(face, box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
