from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import orjson
from rich.progress import Progress

from casehistory.httputil import console
from casehistory.paths import normalized_dir, raw_dir

SECTION_TAGS = {"section"}
NUM_TAGS = {"num"}
HEADING_TAGS = {"heading"}


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1].lower()
    return tag.lower()


def _text_of(el: ET.Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _section_own_text(el: ET.Element) -> str:
    """Text of a section excluding nested <section> elements (those are separate rows)."""
    chunks: list[str] = []
    if el.text:
        chunks.append(el.text)
    for child in list(el):
        if _local(child.tag) in SECTION_TAGS:
            if child.tail:
                chunks.append(child.tail)
            continue
        chunks.append("".join(child.itertext()))
        if child.tail:
            chunks.append(child.tail)
    return " ".join("".join(chunks).split())


def _version_id_from_path(path: Path) -> str:
    m = re.search(r"@([^@/]+)\.zip$", path.name, re.I)
    if m:
        return m.group(1)
    return path.stem


def _extract_sections_from_xml(xml_bytes: bytes, version_id: str, source_file: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    sections: list[dict[str, Any]] = []
    for el in root.iter():
        if _local(el.tag) not in SECTION_TAGS:
            continue
        identifier = el.attrib.get("identifier") or el.attrib.get("id") or ""
        # Skip notes / appendix-style containers without identifiers when empty.
        num = ""
        heading = ""
        for child in list(el):
            ln = _local(child.tag)
            if ln in NUM_TAGS and not num:
                num = (child.attrib.get("value") or _text_of(child)).strip()
            if ln in HEADING_TAGS and not heading:
                heading = _text_of(child)
        body = _section_own_text(el)
        if not identifier and not num and len(body) < 20:
            continue
        title = ""
        m = re.search(r"/t(\d+[a-z]?)/", identifier)
        if m:
            title = m.group(1)
        text_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        sections.append(
            {
                "version_id": version_id,
                "source_file": source_file,
                "identifier": identifier,
                "title": title,
                "num": num,
                "heading": heading,
                "text": body,
                "text_len": len(body),
                "text_sha256_16": text_hash,
            }
        )
    return sections


def normalize(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = raw_dir(cfg, "uscode")
    out_dir = normalized_dir(cfg, "uscode")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sections.jsonl"
    index_path = out_dir / "sections_index.jsonl"

    zips = sorted((raw / "release_points").glob("xml_*.zip"))
    if not zips:
        zips = sorted(raw.rglob("xml_*.zip"))
    console.print(f"[cyan]Normalize US Code:[/] {len(zips)} XML zips → {out_path}")

    total_sections = 0
    versions = 0
    with out_path.open("wb") as out, index_path.open("wb") as idx, Progress(console=console) as progress:
        task = progress.add_task("Parse USLM", total=len(zips))
        for zp in zips:
            version_id = _version_id_from_path(zp)
            try:
                with zipfile.ZipFile(zp) as zf:
                    xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                    if not xml_names:
                        progress.advance(task)
                        continue
                    versions += 1
                    for name in xml_names:
                        data = zf.read(name)
                        rows = _extract_sections_from_xml(data, version_id, f"{zp.name}:{name}")
                        for row in rows:
                            out.write(orjson.dumps(row))
                            out.write(b"\n")
                            # Compact index for browse/diff without loading full text.
                            idx.write(
                                orjson.dumps(
                                    {
                                        "version_id": row["version_id"],
                                        "identifier": row["identifier"],
                                        "title": row["title"],
                                        "num": row["num"],
                                        "heading": row["heading"],
                                        "text_len": row["text_len"],
                                        "text_sha256_16": row["text_sha256_16"],
                                    }
                                )
                            )
                            idx.write(b"\n")
                        total_sections += len(rows)
            except zipfile.BadZipFile:
                console.print(f"[yellow]bad zip: {zp}")
            progress.advance(task)

    return {
        "path": str(out_path),
        "index_path": str(index_path),
        "versions_parsed": versions,
        "sections": total_sections,
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
        "index_bytes": index_path.stat().st_size if index_path.exists() else 0,
    }
