from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import orjson
from rich.progress import Progress

from casehistory.httputil import console
from casehistory.paths import normalized_dir, raw_dir


def _flatten_case(raw: dict[str, Any], volume: int, member: str) -> dict[str, Any]:
    citations = raw.get("citations") or []
    official = next((c.get("cite") for c in citations if c.get("type") == "official"), None)
    opinions = ((raw.get("casebody") or {}).get("opinions")) or []
    texts = [o.get("text") or "" for o in opinions if isinstance(o, dict)]
    full_text = "\n\n".join(t for t in texts if t)
    court = raw.get("court") or {}
    jurisdiction = raw.get("jurisdiction") or {}
    return {
        "cap_id": raw.get("id"),
        "volume": volume,
        "source_member": member,
        "name": raw.get("name"),
        "name_abbreviation": raw.get("name_abbreviation"),
        "decision_date": raw.get("decision_date"),
        "docket_number": raw.get("docket_number"),
        "first_page": raw.get("first_page"),
        "last_page": raw.get("last_page"),
        "citations": citations,
        "official_cite": official,
        "court_name": court.get("name"),
        "court_name_abbreviation": court.get("name_abbreviation"),
        "jurisdiction": jurisdiction.get("name_long") or jurisdiction.get("name"),
        "cites_to": raw.get("cites_to") or [],
        "analysis": raw.get("analysis") or {},
        "opinion_count": len(opinions),
        "text": full_text,
        "text_len": len(full_text),
        # Heuristic flag: true SCOTUS is often "United States" jurisdiction in later volumes;
        # early U.S. Reports mixed courts.
        "is_scotus_jurisdiction": "united states" in (jurisdiction.get("name_long") or "").lower()
        or "u.s." == (jurisdiction.get("name") or "").lower()
        or "supreme court of the united states" in (court.get("name") or "").lower(),
    }


def normalize(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = raw_dir(cfg, "cap", "us")
    out_dir = normalized_dir(cfg, "scotus")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cap_cases.jsonl"

    zips = sorted(raw.glob("*.zip"))
    console.print(f"[cyan]Normalize CAP:[/] {len(zips)} volume zips → {out_path}")

    total = 0
    scotus_tagged = 0
    with out_path.open("wb") as out, Progress(console=console) as progress:
        task = progress.add_task("CAP cases", total=len(zips))
        for zp in zips:
            try:
                volume = int(zp.stem)
            except ValueError:
                volume = -1
            try:
                with zipfile.ZipFile(zp) as zf:
                    for name in zf.namelist():
                        if not name.startswith("json/") or not name.endswith(".json"):
                            continue
                        raw_case = orjson.loads(zf.read(name))
                        row = _flatten_case(raw_case, volume, name)
                        if row["is_scotus_jurisdiction"]:
                            scotus_tagged += 1
                        out.write(orjson.dumps(row))
                        out.write(b"\n")
                        total += 1
            except zipfile.BadZipFile:
                console.print(f"[yellow]bad zip: {zp}")
            progress.advance(task)

    return {
        "path": str(out_path),
        "cases": total,
        "scotus_jurisdiction_tagged": scotus_tagged,
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
    }
