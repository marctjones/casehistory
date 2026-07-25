from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

from casehistory.httputil import console, download_file, make_client
from casehistory.manifest import Manifest
from casehistory.paths import raw_dir


def collect(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    src = cfg["sources"]["scdb"]
    if not src.get("enabled", True):
        return {"source": "scdb", "status": "disabled"}

    base = src["base_url"].rstrip("/") + "/"
    out_dir = raw_dir(cfg, "scdb")
    manifest = Manifest(raw_dir(cfg).parent / "manifest.sqlite")
    client = make_client(cfg)

    # Map filenames to prefixes.
    modern = src.get("modern_prefix", "_brickFiles/2025_01").strip("/")
    legacy = src.get("legacy_prefix", "_brickFiles/Legacy_07").strip("/")
    files = list(src.get("files") or [])

    jobs: list[tuple[str, str]] = []
    for name in files:
        if "Legacy" in name or "legacy" in name:
            rel = f"{legacy}/{name}"
        else:
            rel = f"{modern}/{name}"
        jobs.append((name, urljoin(base, rel)))

    console.print(f"[cyan]SCDB:[/] {len(jobs)} bulk files")
    results: list[dict[str, Any]] = []
    try:

        def one(item: tuple[str, str]) -> dict[str, Any]:
            name, url = item
            dest = out_dir / name
            rec = download_file(client, url, dest, force=force)
            manifest.upsert("scdb", name, rec)
            return rec

        with ThreadPoolExecutor(max_workers=min(4, int(cfg.get("concurrency", 4)))) as pool:
            futs = [pool.submit(one, j) for j in jobs]
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]SCDB failed: {exc}")
                    results.append({"status": "error", "error": str(exc)})

        ok = sum(1 for r in results if r.get("status") in {"downloaded", "exists"})
        return {"source": "scdb", "ok": ok, "errors": len(results) - ok, "dir": str(out_dir)}
    finally:
        client.close()
        manifest.close()
