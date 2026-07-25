from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from rich.progress import Progress

from casehistory.httputil import console, download_file, get_text, make_client
from casehistory.manifest import Manifest
from casehistory.paths import raw_dir


def _list_volumes(client, base_url: str) -> list[int]:
    html = get_text(client, base_url.rstrip("/") + "/")
    vols = sorted({int(v) for v in re.findall(r"/us/(\d+)\.zip", html)})
    if not vols:
        # fallback: probe sequential until a streak of misses
        found: list[int] = []
        misses = 0
        v = 1
        while misses < 5 and v < 2000:
            url = f"{base_url.rstrip('/')}/{v}.zip"
            resp = client.head(url)
            if resp.status_code == 200:
                found.append(v)
                misses = 0
            else:
                misses += 1
            v += 1
        vols = found
    return vols


def collect(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    src = cfg["sources"]["cap_us_reports"]
    if not src.get("enabled", True):
        return {"source": "cap_us_reports", "status": "disabled"}

    base = src["base_url"].rstrip("/")
    out_dir = raw_dir(cfg, "cap", "us")
    manifest = Manifest(raw_dir(cfg).parent / "manifest.sqlite")
    client = make_client(cfg)
    try:
        volumes = _list_volumes(client, base)
        vr = src.get("volume_range")
        if vr is not None:
            lo, hi = int(vr[0]), int(vr[1])
            volumes = [v for v in volumes if lo <= v <= hi]

        formats = src.get("formats") or ["zip"]
        jobs: list[tuple[int, str, str]] = []
        for v in volumes:
            for fmt in formats:
                ext = fmt if fmt.startswith(".") else f".{fmt}"
                # CAP uses us/1.zip not us/1.zip only for zip; pdf/tar similarly
                filename = f"{v}{ext}"
                url = f"{base}/{filename}"
                jobs.append((v, url, filename))

        console.print(f"[cyan]CAP U.S. Reports:[/] {len(volumes)} volumes × {len(formats)} format(s)")
        concurrency = int(cfg.get("concurrency", 6))
        results: list[dict[str, Any]] = []

        def one(job: tuple[int, str, str]) -> dict[str, Any]:
            v, url, filename = job
            dest = out_dir / filename
            rec = download_file(client, url, dest, force=force)
            rec["volume"] = v
            manifest.upsert("cap_us_reports", filename, rec)
            return rec

        with Progress(console=console) as progress:
            task = progress.add_task("CAP volumes", total=len(jobs))
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futs = [pool.submit(one, j) for j in jobs]
                for fut in as_completed(futs):
                    try:
                        results.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"[red]CAP download failed: {exc}")
                        results.append({"status": "error", "error": str(exc)})
                    progress.advance(task)

        ok = sum(1 for r in results if r.get("status") in {"downloaded", "exists"})
        return {
            "source": "cap_us_reports",
            "volumes": len(volumes),
            "ok": ok,
            "errors": len(results) - ok,
            "dir": str(out_dir),
        }
    finally:
        client.close()
        manifest.close()
