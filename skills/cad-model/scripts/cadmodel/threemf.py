# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""A deterministic 3MF writer and a strict reader, stdlib only.

The package targets 3MF Core 1.4.0 (ISO/IEC 25422:2025) in millimetres. It has three entries in a fixed order,
stored without compression, with fixed timestamps and attributes. Floats use one fixed format, so the same
mesh always gives the same bytes. It has no Production, Slice or Toolpath extension and no UUIDs. `EXTENSIONS`
lists the namespaces of the model element. It is the one place to add Materials 1.2.1 or Production 1.1.2.

`read` accepts only what `dumps` writes, and refuses a package whose parts, units, ids or indices do not
resolve. `measure` judges a mesh by its indices: a crack, a flipped triangle and an unwelded vertex all show.
"""

from __future__ import annotations

import io
import math
import re
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
DECIMALS = 6  # nanometres: the writer rounds and welds every coordinate at this precision
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'

MATERIAL_METADATA = "cadagent:material"
COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")  # sRGB, as part.yaml intent.color spells it
RESOURCE_ID_LIMIT = 2 ** 31  # ST_ResourceID: a positive integer below this


@dataclass(frozen=True)
class Namespace:
    prefix: str
    uri: str
    required: bool = False  # listed in the model's `requiredextensions`


# The producer's own metadata namespace. Metadata names outside the core namespace must carry a prefix declared
# on the model element, and an optional namespace is ignorable to a consumer that does not know it.
PRODUCER = Namespace("cadagent", "https://github.com/benchhand/benchhand/ns/3mf/2026")
# Extensions the model declares. Empty: the package is Core only. Materials 1.2.1 would add
# Namespace("m", "http://schemas.microsoft.com/3dmanufacturing/material/2015/02") here, Production 1.1.2
# Namespace("p", "http://schemas.microsoft.com/3dmanufacturing/production/2015/06") with uuid5 identifiers.
EXTENSIONS: tuple[Namespace, ...] = ()


class ThreeMFError(ValueError):
    """The package has a part, unit, id or index that does not resolve, or a value `dumps` refuses."""


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
    color: str | None = None  # `#RRGGBBAA` of the basematerials entry the object points at


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


def _model(vertices: Iterable[Sequence[float]], triangles: Iterable[Sequence[int]], name: str, material: str | None,
           color: str | None) -> str:
    metadata = (f'<metadatagroup><metadata name="{MATERIAL_METADATA}">{escape(material)}</metadata></metadatagroup>'
                if material is not None else "")
    points = "".join(f'<vertex x="{number(x)}" y="{number(y)}" z="{number(z)}"/>' for x, y, z in vertices)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
    # Resource ids share one space: the color group takes 1, the object the next free id.
    group, ident, pid = "", 1, ""
    if color is not None:
        group = (f'<basematerials id="1"><base name={quoteattr(material or name)} '
                 f'displaycolor="{color.upper()}FF"/></basematerials>')
        ident, pid = 2, ' pid="1" pindex="0"'
    return (f"{XML_DECLARATION}<model {_namespaces()}><resources>{group}"
            f'<object id="{ident}" name={quoteattr(name)} type="model"{pid}>{metadata}'
            f"<mesh><vertices>{points}</vertices><triangles>{faces}</triangles></mesh></object>"
            f'</resources><build><item objectid="{ident}"/></build></model>')


def dumps(vertices: Iterable[Sequence[float]], triangles: Iterable[Sequence[int]], *, name: str,
          material: str | None = None, color: str | None = None) -> bytes:
    """The package of one mesh object, `name`d, with `material` as its `cadagent:material` metadata when given.

    `color` (`#RRGGBB`) adds a basematerials group with that display color and points the object at it. Without
    it the package has no basematerials and no pid.

    The mesh goes in as it is, so wind and weld it first. The unit is millimetres. The build item has no
    transform, so the vertices already sit in the print pose."""
    if color is not None and not COLOR_PATTERN.fullmatch(color):
        raise ThreeMFError(f"colour {color!r} is not #RRGGBB")
    contents = {CONTENT_TYPES_PART: _content_types(), RELS_PART: _relationships(),
                MODEL_PART: _model(vertices, triangles, name, material, color)}
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
          material: str | None = None, color: str | None = None) -> None:
    path.write_bytes(dumps(vertices, triangles, name=name, material=material, color=color))


def _tag(element: ET.Element) -> str:
    return element.tag.removeprefix(f"{{{CORE_NS}}}")


def _parse(data: bytes, what: str) -> tuple[ET.Element, set[str]]:
    """The root of an XML part and the namespace prefixes it declares."""
    parser: Any = ET.XMLPullParser(("start-ns",))
    try:
        parser.feed(data)
        parser.close()
        events: list[tuple[str, Any]] = list(parser.read_events())  # start-ns events carry (prefix, uri)
        prefixes = {namespace[0] for _, namespace in events}
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


def _resource_id(element: ET.Element, what: str) -> int:
    """The id of a resource element, within the ST_ResourceID range."""
    ident = _int(element.get("id"), f"{what} id")
    if not 0 < ident < RESOURCE_ID_LIMIT:
        raise ThreeMFError(f"{what} id {ident} is outside 1..{RESOURCE_ID_LIMIT - 1}")
    return ident


def _color(element: ET.Element, groups: dict[int, list[str]]) -> str | None:
    """The display color of the basematerials entry `element` points at by pid and pindex, if any."""
    if element.get("pid") is None:
        return None
    pid, index = _int(element.get("pid"), "object pid"), _int(element.get("pindex"), "object pindex")
    if pid not in groups or not 0 <= index < len(groups[pid]):
        raise ThreeMFError(f"object pid {pid} pindex {index} does not resolve to a basematerials entry")
    return groups[pid][index]


def _object(element: ET.Element, prefixes: set[str], groups: dict[int, list[str]]) -> Object:
    ident = _resource_id(element, "object")
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
    return Object(ident, element.get("name"), metadata, vertices, triangles, _color(element, groups))


def read(source: Path | bytes) -> Model:
    """The model of a package, after checking its entries, content types, relationship, unit and that every
    resource id (basematerials group or object) is unique, every triangle index and every build item's object
    resolves."""
    try:
        with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as archive:
            _check_envelope(archive)
            root, prefixes = _parse(archive.read(MODEL_PART), MODEL_PART)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:  # the latter two: encrypted, unknown method
        raise ThreeMFError(f"not a readable ZIP package: {error}") from error
    if root.tag != f"{{{CORE_NS}}}model":
        raise ThreeMFError(f"{MODEL_PART} root is {root.tag}, not a 3MF model")
    if root.get("requiredextensions"):
        raise ThreeMFError(f"the model requires extensions {root.get('requiredextensions')!r}; this reader has none")
    if root.get("unit") != UNIT:
        raise ThreeMFError(f"the unit is {root.get('unit')!r}, not {UNIT!r}")
    elements = list(root.iterfind(f"{{{CORE_NS}}}resources/{{{CORE_NS}}}basematerials"))
    group_ids = [_resource_id(g, "basematerials") for g in elements]
    object_ids = [_resource_id(e, "object") for e in root.iterfind(f"{{{CORE_NS}}}resources/{{{CORE_NS}}}object")]
    shared = group_ids + object_ids
    if len(set(shared)) != len(shared):
        raise ThreeMFError(f"resource ids repeat among basematerials groups {group_ids} and objects {object_ids}")
    groups = {ident: [b.get("displaycolor") or "" for b in g] for ident, g in zip(group_ids, elements)}
    objects = [_object(e, prefixes, groups) for e in root.iterfind(f"{{{CORE_NS}}}resources/{{{CORE_NS}}}object")]
    ids = object_ids
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
    """Measure a mesh by its indices alone, as an importer reads it. It welds and repairs nothing. `watertight`
    holds when exactly two triangles use every edge. `winding_consistent` holds when one triangle uses every
    directed edge. The result also has the signed volume (positive when wound outward), the area and the
    bounding box."""
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
