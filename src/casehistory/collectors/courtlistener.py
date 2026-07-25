from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import orjson
from rich.progress import Progress

from casehistory.httputil import (
    DownloadError,
    console,
    download_file,
    make_client,
    polite_sleep,
)
from casehistory.manifest import Manifest
from casehistory.paths import raw_dir


def _s3_url(bucket: str, key: str, region: str) -> str:
    return f"https://{bucket}.s3-{region}.amazonaws.com/{key}"


def _latest_bulk_key(client, bucket: str, prefix: str, region: str, name_prefix: str) -> str | None:
    """List S3 bulk-data keys and pick the newest non-tiny file for a prefix."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    token = None
    best: tuple[str, int] | None = None
    while True:
        q = f"list-type=2&prefix={quote(prefix)}&max-keys=1000"
        if token:
            q += f"&continuation-token={quote(token, safe='')}"
        url = f"https://{bucket}.s3-{region}.amazonaws.com/?{q}"
        resp = client.get(url)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for c in root.findall("s3:Contents", ns):
            key = c.find("s3:Key", ns).text or ""
            size = int(c.find("s3:Size", ns).text or 0)
            fname = key.rsplit("/", 1)[-1]
            if not fname.startswith(name_prefix) or size < 1000:
                continue
            if best is None or fname > best[0]:
                best = (fname, size)
        cont = root.find("s3:NextContinuationToken", ns)
        trunc = root.find("s3:IsTruncated", ns)
        if trunc is not None and trunc.text == "true" and cont is not None and cont.text:
            token = cont.text
        else:
            break
    if not best:
        return None
    return f"{prefix}{best[0]}"


def _get_json_with_retry(client, url: str, *, max_attempts: int = 8) -> Any:
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        resp = client.get(url)
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            wait = float(ra) if ra and ra.isdigit() else delay
            console.print(f"[yellow]CL rate limit; sleeping {wait:.0f}s (attempt {attempt})")
            time.sleep(wait)
            delay = min(delay * 2, 120)
            continue
        if resp.status_code >= 500:
            console.print(f"[yellow]CL HTTP {resp.status_code}; retry in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        if resp.status_code >= 400:
            raise DownloadError(f"HTTP {resp.status_code} for {url}")
        return resp.json()
    raise DownloadError(f"Gave up after rate limits for {url}")


def collect(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    src = cfg["sources"]["courtlistener"]
    if not src.get("enabled", True):
        return {"source": "courtlistener", "status": "disabled"}

    out_dir = raw_dir(cfg, "courtlistener")
    meta_path = out_dir / "scotus_search.jsonl"
    state_path = out_dir / "scotus_search.state.json"
    manifest = Manifest(raw_dir(cfg).parent / "manifest.sqlite")
    client = make_client(cfg)
    stats: dict[str, Any] = {"source": "courtlistener"}

    try:
        if src.get("download_bulk_courts_table", True):
            key = _latest_bulk_key(
                client,
                src["bulk_s3_bucket"],
                src["bulk_s3_prefix"],
                src["bulk_s3_region"],
                "courts-",
            )
            if key:
                url = _s3_url(src["bulk_s3_bucket"], key, src["bulk_s3_region"])
                dest = out_dir / "bulk" / Path(key).name
                rec = download_file(client, url, dest, force=force)
                manifest.upsert("courtlistener", Path(key).name, rec)
                stats["courts_bulk"] = rec

        params = {
            "type": "o",
            "court": src.get("court_id", "scotus"),
            "order_by": "dateFiled desc",
            "page_size": int(src.get("page_size", 100)),
        }
        max_pages = src.get("max_pages")
        page = 0
        written = 0
        url: str | None = f"{src['search_url']}?{urlencode(params)}"

        # Resume support: if state has next URL and not force, continue.
        if force:
            meta_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
        elif state_path.exists():
            state = orjson.loads(state_path.read_bytes())
            if state.get("next"):
                url = state["next"]
                page = int(state.get("page", 0))
                written = int(state.get("records", 0))
                console.print(
                    f"[cyan]CourtListener:[/] resuming at page={page} records={written}"
                )
            elif state.get("done") and meta_path.exists() and meta_path.stat().st_size > 0:
                console.print(f"[cyan]CourtListener:[/] complete snapshot at {meta_path}")
                stats["search_jsonl"] = str(meta_path)
                stats["search_bytes"] = meta_path.stat().st_size
                stats["status"] = "exists"
                return stats

        meta_path.parent.mkdir(parents=True, exist_ok=True)
        console.print("[cyan]CourtListener:[/] paginating SCOTUS search API…")
        mode = "ab" if meta_path.exists() and written > 0 else "wb"

        with meta_path.open(mode) as out, Progress(console=console) as progress:
            task = progress.add_task("CL search pages", total=max_pages)
            while url:
                data = _get_json_with_retry(client, url)
                if page == 0 and written == 0:
                    stats["reported_count"] = data.get("count")
                    console.print(f"  reported count={data.get('count')}")
                for row in data.get("results") or []:
                    out.write(orjson.dumps(row))
                    out.write(b"\n")
                    written += 1
                page += 1
                progress.advance(task)
                url = data.get("next")
                state_path.write_bytes(
                    orjson.dumps(
                        {
                            "next": url,
                            "page": page,
                            "records": written,
                            "done": url is None,
                        }
                    )
                )
                if max_pages is not None and page >= int(max_pages):
                    break
                # Stay under unauthenticated rate limits.
                polite_sleep(float(src.get("page_delay_seconds", 0.35)))

        rec = {
            "url": src["search_url"],
            "path": str(meta_path),
            "bytes": meta_path.stat().st_size,
            "status": "downloaded",
            "records": written,
            "pages": page,
        }
        manifest.upsert("courtlistener", "scotus_search.jsonl", rec)
        stats.update({"search_records": written, "pages": page, "path": str(meta_path)})
        return stats
    finally:
        client.close()
        manifest.close()
