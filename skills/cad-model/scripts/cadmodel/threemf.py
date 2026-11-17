"""A deterministic 3MF writer and a strict reader, stdlib only.

The package targets 3MF Core 1.4.0 (ISO/IEC 25422:2025) in millimetres: three entries in a fixed order,
stored without compression, with fixed timestamps and attributes, and floats in one fixed format, so the same
mesh always gives the same bytes. It has no Production, Slice or Toolpath extension and no UUIDs. The model
element's namespaces are listed in `EXTENSIONS`, the one place where Materials 1.2.1 or Production 1.1.2 would
be added.

`read` accepts only what `dumps` writes, and refuses a package whose parts, units, ids or indices do not
resolve. `measure` judges a mesh by its indices: a crack, a flipped triangle and an unwelded vertex all show.
"""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape, quoteattr

VERSION = "3MF Core 1.4.0"
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_RELATIONSHIP = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
MODEL_CONTENT_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
RELS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
UNIT = "millimeter"
CONTENT_TYPES_PART, RELS_PART, MODEL_PART = "[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"
PARTS = (CONTENT_TYPES_PART, RELS_PART, MODEL_PART)  # the entry order of every package
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
ZIP_EXTERNAL_ATTR = 0o644 << 16
ZIP_CREATE_SYSTEM = 3  # Unix
DECIMALS = 6  # nanometres: a coordinate is written, and welded, at this precision
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'

MATERIAL_METADATA = "cadagent:material"


@dataclass(frozen=True)
class Namespace:
    prefix: str
    uri: str
    required: bool = False  # listed in the model's `requiredextensions`


# The producer's own metadata namespace. Metadata names outside the core namespace must carry a prefix declared
# on the model element, and an optional namespace is ignorable to a consumer that does not know it.
PRODUCER = Namespace("cadagent", "https://github.com/srobroek/cad-agent/ns/3mf/2026")
# Extensions the model declares. Empty: the package is Core only. Materials 1.2.1 would add
# Namespace("m", "http://schemas.microsoft.com/3dmanufacturing/material/2015/02") here, Production 1.1.2
# Namespace("p", "http://schemas.microsoft.com/3dmanufacturing/production/2015/06") with uuid5 identifiers.
EXTENSIONS: tuple[Namespace, ...] = ()


class ThreeMFError(ValueError):
    """The package is not one this module writes: a part, unit, id or index that does not resolve."""


@dataclass(frozen=True)
class Item:
    objectid: int
    transform: tuple[float, ...] | None


@dataclass(frozen=True)
class Object:
    id: int
    name: str | None
    metadata: dict[str, str]
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]


@dataclass(frozen=True)
class Model:
    unit: str
    objects: list[Object]
    build: list[Item]


def number(value: float) -> str:
    """`value` in the one float format of the package: six decimals, never a negative zero."""
    text = f"{value:.{DECIMALS}f}"
    return text[1:] if text == f"-{0:.{DECIMALS}f}" else text


def _namespaces() -> str:
    declared = [PRODUCER, *EXTENSIONS]
    attributes = [f'unit="{UNIT}"', f'xmlns="{CORE_NS}"', *(f'xmlns:{ns.prefix}="{ns.uri}"' for ns in declared)]
    required = " ".join(ns.prefix for ns in declared if ns.required)
    if required:
        attributes.append(f'requiredextensions="{required}"')
    return " ".join(attributes)


def _content_types() -> str:
    return (f'{XML_DECLARATION}<Types xmlns="{CONTENT_TYPES_NS}">'
            f'<Default Extension="rels" ContentType="{RELS_CONTENT_TYPE}"/>'
            f'<Default Extension="model" ContentType="{MODEL_CONTENT_TYPE}"/></Types>')


def _relationships() -> str:
    return (f'{XML_DECLARATION}<Relationships xmlns="{RELATIONSHIPS_NS}">'
            f'<Relationship Target="/{MODEL_PART}" Id="rel0" Type="{MODEL_RELATIONSHIP}"/></Relationships>')


def _model(vertices: Iterable[Sequence[float]], triangles: Iterable[Sequence[int]], name: str, material: str | None) -> str:
    metadata = (f'<metadatagroup><metadata name="{MATERIAL_METADATA}">{escape(material)}</metadata></metadatagroup>'
                if material is not None else "")
    points = "".join(f'<vertex x="{number(x)}" y="{number(y)}" z="{number(z)}"/>' for x, y, z in vertices)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
    return (f"{XML_DECLARATION}<model {_namespaces()}><resources>"
            f'<object id="1" name={quoteattr(name)} type="model">{metadata}'
            f"<mesh><vertices>{points}</vertices><triangles>{faces}</triangles></mesh></object>"
            '</resources><build><item objectid="1"/></build></model>')


def dumps(vertices: Iterable[Sequence[float]], triangles: Iterable[Sequence[int]], *, name: str,
          material: str | None = None) -> bytes:
    """The package of one mesh object, `name`d, with `material` as its `cadagent:material` metadata when given.

    The mesh is taken as it is: wind and weld it before. Millimetres; the build item has no transform, so
    the vertices are in the pose they are to be made in."""
    contents = {CONTENT_TYPES_PART: _content_types(), RELS_PART: _relationships(),
                MODEL_PART: _model(vertices, triangles, name, material)}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for part in PARTS:
            info = zipfile.ZipInfo(part, ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = ZIP_EXTERNAL_ATTR
            info.create_system = ZIP_CREATE_SYSTEM
            archive.writestr(info, contents[part].encode("utf-8"))
    return out.getvalue()


def write(path: Path, vertices: Iterable[Sequence[float]], triangles: Iterable[Sequence[int]], *, name: str,
          material: str | None = None) -> None:
    path.write_bytes(dumps(vertices, triangles, name=name, material=material))


def _tag(element: ET.Element) -> str:
    return element.tag.removeprefix(f"{{{CORE_NS}}}")


def _parse(data: bytes, what: str) -> tuple[ET.Element, set[str]]:
    """The root of an XML part and the namespace prefixes it declares."""
    parser = ET.XMLPullParser(("start-ns",))
    try:
        parser.feed(data)
        parser.close()
        prefixes = {prefix for _, (prefix, _) in parser.read_events()}
        return ET.fromstring(data), prefixes
    except ET.ParseError as error:
        raise ThreeMFError(f"{what} is not well-formed XML: {error}") from error


def _int(text: str | None, what: str) -> int:
    try:
        return int(text or "")
    except ValueError as error:
        raise ThreeMFError(f"{what} is not an integer: {text!r}") from error


def _float(text: str | None, what: str) -> float:
    try:
        value = float(text or "")
    except ValueError as error:
        raise ThreeMFError(f"{what} is not a number: {text!r}") from error
    if not math.isfinite(value):
        raise ThreeMFError(f"{what} is not finite: {text!r}")
    return value


def _check_envelope(archive: zipfile.ZipFile) -> None:
    if archive.namelist() != list(PARTS):
        raise ThreeMFError(f"the entries are {archive.namelist()}, not {list(PARTS)}")
    types, _ = _parse(archive.read(CONTENT_TYPES_PART), CONTENT_TYPES_PART)
    defaults = {e.get("Extension"): e.get("ContentType") for e in types if e.tag == f"{{{CONTENT_TYPES_NS}}}Default"}
    if defaults.get("model") != MODEL_CONTENT_TYPE or defaults.get("rels") != RELS_CONTENT_TYPE:
        raise ThreeMFError(f"{CONTENT_TYPES_PART} does not declare the model and relationship content types")
    rels, _ = _parse(archive.read(RELS_PART), RELS_PART)
    targets = [e.get("Target") for e in rels if e.get("Type") == MODEL_RELATIONSHIP]
    if targets != [f"/{MODEL_PART}"]:
        raise ThreeMFError(f"{RELS_PART} relates the model to {targets}, not ['/{MODEL_PART}']")


def _object(element: ET.Element, prefixes: set[str]) -> Object:
    ident = _int(element.get("id"), "object id")
    metadata: dict[str, str] = {}
    mesh = None
    for child in element:
        if _tag(child) == "metadatagroup":
            for item in child:
                name = item.get("name") or ""
                if ":" not in name or name.split(":", 1)[0] not in prefixes:
                    raise ThreeMFError(f"object {ident} metadata {name!r} has no declared namespace prefix")
                metadata[name] = item.text or ""
        elif _tag(child) == "mesh":
            mesh = child
    if mesh is None:
        raise ThreeMFError(f"object {ident} has no mesh")
    vertices = [(_float(v.get("x"), "x"), _float(v.get("y"), "y"), _float(v.get("z"), "z"))
                for v in mesh.iterfind(f"{{{CORE_NS}}}vertices/{{{CORE_NS}}}vertex")]
    triangles = [(_int(t.get("v1"), "v1"), _int(t.get("v2"), "v2"), _int(t.get("v3"), "v3"))
                 for t in mesh.iterfind(f"{{{CORE_NS}}}triangles/{{{CORE_NS}}}triangle")]
    for triangle in triangles:
        if not all(0 <= index < len(vertices) for index in triangle) or len(set(triangle)) < 3:
            raise ThreeMFError(f"object {ident} has a triangle {triangle} outside its {len(vertices)} vertices")
    return Object(ident, element.get("name"), metadata, vertices, triangles)


def read(source: Path | bytes) -> Model:
    """The model of a package, after checking its entries, content types, relationship, unit and that every
    object id is unique, every triangle index and every build item's object resolves."""
    try:
        with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as archive:
            _check_envelope(archive)
            root, prefixes = _parse(archive.read(MODEL_PART), MODEL_PART)
    except zipfile.BadZipFile as error:
        raise ThreeMFError(f"not a ZIP package: {error}") from error
    if root.tag != f"{{{CORE_NS}}}model":
        raise ThreeMFError(f"{MODEL_PART} root is {root.tag}, not a 3MF model")
    if root.get("unit") != UNIT:
        raise ThreeMFError(f"the unit is {root.get('unit')!r}, not {UNIT!r}")
    objects = [_object(e, prefixes) for e in root.iterfind(f"{{{CORE_NS}}}resources/{{{CORE_NS}}}object")]
    ids = [obj.id for obj in objects]
    if len(set(ids)) != len(ids):
        raise ThreeMFError(f"object ids repeat: {ids}")
    build = []
    for item in root.iterfind(f"{{{CORE_NS}}}build/{{{CORE_NS}}}item"):
        objectid = _int(item.get("objectid"), "build item objectid")
        if objectid not in ids:
            raise ThreeMFError(f"build item names object {objectid}, which is not among {ids}")
        text = item.get("transform")
        transform = tuple(_float(v, "transform") for v in text.split()) if text else None
        if transform is not None and len(transform) != 12:
            raise ThreeMFError(f"a build item transform has {len(transform)} numbers, not 12")
        build.append(Item(objectid, transform))
    return Model(root.get("unit") or "", objects, build)


def measure(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]) -> dict[str, Any]:
    """What an importer sees of a mesh, by its indices alone (nothing is welded or repaired): `watertight`
    when every edge is used by exactly two triangles, `winding_consistent` when every directed edge is
    used once, the signed volume (positive when wound outward), the area and the bounding box."""
    directed = Counter((a, b) for t in triangles for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))
    undirected: Counter[tuple[int, int]] = Counter()
    for (a, b), count in directed.items():
        undirected[(min(a, b), max(a, b))] += count
    volume = area = 0.0
    for a, b, c in triangles:
        p, q, r = vertices[a], vertices[b], vertices[c]
        volume += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0])
                   + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        area += 0.5 * math.sqrt((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2
                                + (u[0] * v[1] - u[1] * v[0]) ** 2)
    used = sorted({index for t in triangles for index in t})
    low = [min(vertices[i][axis] for i in used) if used else 0.0 for axis in range(3)]
    high = [max(vertices[i][axis] for i in used) if used else 0.0 for axis in range(3)]
    return {"triangles": len(triangles), "vertices": len(vertices),
            "watertight": bool(triangles) and all(count == 2 for count in undirected.values()),
            "winding_consistent": bool(triangles) and all(count == 1 for count in directed.values()),
            "volume_mm3": volume, "area_mm2": area, "bbox_mm": {"min": low, "max": high}}
