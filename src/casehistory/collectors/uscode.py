from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from rich.progress import Progress

from casehistory.httputil import console, download_file, get_text, make_client
from casehistory.manifest import Manifest
from casehistory.paths import raw_dir

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _parse_release_point_links(html: str, base: str) -> list[dict[str, str]]:
    soup = _soup(html)
    points: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join(a.get_text(" ", strip=True).split())
        if "usc-rp@" in href or "/releasepoints/" in href:
            points.append({"url": urljoin(base + "/", href), "label": text})
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in points:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        out.append(p)
    return out


def _zip_links_from_page(html: str, page_url: str, prefer: str) -> list[dict[str, str]]:
    soup = _soup(html)
    zips: list[dict[str, str]] = []
    prefer = prefer.lower()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".zip"):
            continue
        name = href.split("/")[-1].lower()
        kind = "other"
        if name.startswith("xml_") or "/xml_" in href.lower() or "xml" in name[:4]:
            kind = "xml"
        elif name.startswith("htm_") or "xhtml" in name or name.startswith("htm"):
            kind = "xhtml"
        elif name.startswith("pdf_"):
            kind = "pdf"
        elif name.startswith("pcc_"):
            kind = "pcc"
        label = a.get_text(" ", strip=True) or name
        zips.append(
            {
                "url": urljoin(page_url, href),
                "label": label,
                "kind": kind,
                "filename": href.split("/")[-1],
            }
        )

    # Prefer "all titles" archives of the desired format.
    preferred = [
        z
        for z in zips
        if z["kind"] == prefer and ("all" in z["filename"].lower() or "uscAll" in z["filename"] or "uscall" in z["filename"].lower())
    ]
    if preferred:
        return preferred
    same_kind = [z for z in zips if z["kind"] == prefer]
    return same_kind or zips


def _discover_current_and_prior(client, cfg_src: dict[str, Any]) -> list[dict[str, Any]]:
    base = cfg_src["base_url"].rstrip("/")
    prefer = cfg_src.get("prefer_format", "xml")
    jobs: list[dict[str, Any]] = []

    download_html = get_text(client, f"{base}/download.shtml")
    for z in _zip_links_from_page(download_html, f"{base}/download.shtml", prefer):
        jobs.append({**z, "release": "current", "page": f"{base}/download.shtml"})

    prior_url = f"{base}/priorreleasepoints.htm"
    prior_html = get_text(client, prior_url)
    points = _parse_release_point_links(prior_html, base)
    max_pts = cfg_src.get("max_release_points")
    # First entry on prior page is often the current release too; keep discovery order.
    if max_pts is not None:
        points = points[: int(max_pts)]

    for pt in points:
        try:
            html = get_text(client, pt["url"])
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]skip release page {pt['url']}: {exc}")
            continue
        for z in _zip_links_from_page(html, pt["url"], prefer):
            jobs.append(
                {
                    **z,
                    "release": pt["label"] or pt["url"],
                    "page": pt["url"],
                }
            )

    # Annual archives index (XHTML/PDF lists)
    if cfg_src.get("include_annual_archives", True):
        for page_name in (
            "annualhistoricalarchives/downloadxhtml.shtml",
            "annualhistoricalarchives/downloadPDF.shtml",
        ):
            url = f"{base}/{page_name}"
            try:
                html = get_text(client, url)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]skip annual page {url}: {exc}")
                continue
            soup = _soup(html)
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Prefer bulk zips over individual PDFs.
                if not re.search(r"\.zip$", href, re.I):
                    continue
                jobs.append(
                    {
                        "url": urljoin(url, href),
                        "label": a.get_text(" ", strip=True) or href,
                        "kind": "annual",
                        "filename": href.split("/")[-1],
                        "release": "annual",
                        "page": url,
                    }
                )

    # de-dupe by URL
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for j in jobs:
        if j["url"] in seen:
            continue
        seen.add(j["url"])
        unique.append(j)
    return unique


def collect(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    src = cfg["sources"]["uscode"]
    if not src.get("enabled", True):
        return {"source": "uscode", "status": "disabled"}

    out_dir = raw_dir(cfg, "uscode")
    manifest = Manifest(raw_dir(cfg).parent / "manifest.sqlite")
    client = make_client(cfg)
    try:
        jobs = _discover_current_and_prior(client, src)
        console.print(f"[cyan]US Code:[/] discovered {len(jobs)} downloadable archives")

        results: list[dict[str, Any]] = []
        concurrency = int(cfg.get("concurrency", 4))

        def one(job: dict[str, Any]) -> dict[str, Any]:
            # group: release_points vs annual
            sub = "annual" if job.get("kind") == "annual" or job.get("release") == "annual" else "release_points"
            dest = out_dir / sub / job["filename"]
            rec = download_file(client, job["url"], dest, force=force)
            rec["release"] = job.get("release")
            rec["kind"] = job.get("kind")
            rec["page"] = job.get("page")
            key = f"{sub}/{job['filename']}"
            manifest.upsert("uscode", key, rec)
            return rec

        with Progress(console=console) as progress:
            task = progress.add_task("US Code downloads", total=len(jobs))
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futs = [pool.submit(one, j) for j in jobs]
                for fut in as_completed(futs):
                    try:
                        results.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"[red]US Code download failed: {exc}")
                        results.append({"status": "error", "error": str(exc)})
                    progress.advance(task)

        ok = sum(1 for r in results if r.get("status") in {"downloaded", "exists"})
        return {
            "source": "uscode",
            "discovered": len(jobs),
            "ok": ok,
            "errors": len(results) - ok,
            "dir": str(out_dir),
        }
    finally:
        client.close()
        manifest.close()
