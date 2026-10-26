"""Low-level access to a .docx package: parts, relationships, namespaces."""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "v": "urn:schemas-microsoft-com:vml",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
W = NS["w"]


def q(tag: str) -> str:
    """'w:p' -> '{ns}p'."""
    prefix, local = tag.split(":")
    return "{%s}%s" % (NS[prefix], local)


def wval(el, attr: str = "val"):
    return el.get(q("w:" + attr)) if el is not None else None


@dataclass
class Rels:
    """Relationships of one part, id -> (target part name, type, mode)."""
    by_id: dict[str, tuple[str, str, str | None]] = field(default_factory=dict)

    def target(self, rid: str) -> str | None:
        hit = self.by_id.get(rid)
        return hit[0] if hit else None


class DocxPackage:
    def __init__(self, path: str):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        self.names = set(self.zip.namelist())
        self._xml_cache: dict[str, etree._Element] = {}

    def has(self, name: str) -> bool:
        return name in self.names

    def read(self, name: str) -> bytes:
        return self.zip.read(name)

    def xml(self, name: str) -> etree._Element:
        if name not in self._xml_cache:
            parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
            self._xml_cache[name] = etree.fromstring(self.zip.read(name), parser)
        return self._xml_cache[name]

    def rels(self, part: str) -> Rels:
        """Relationships for a part, e.g. 'word/document.xml' -> word/_rels/document.xml.rels."""
        d, b = posixpath.split(part)
        rels_name = posixpath.join(d, "_rels", b + ".rels")
        out = Rels()
        if rels_name not in self.names:
            return out
        root = self.xml(rels_name)
        for r in root.findall("rel:Relationship", NS):
            target = r.get("Target")
            mode = r.get("TargetMode")
            if mode == "External":
                resolved = target
            else:
                resolved = posixpath.normpath(posixpath.join(d, target)) if not target.startswith("/") else target.lstrip("/")
            out.by_id[r.get("Id")] = (resolved, r.get("Type", ""), mode)
        return out

    def media_parts(self) -> list[str]:
        return sorted(n for n in self.names if n.startswith("word/media/"))
