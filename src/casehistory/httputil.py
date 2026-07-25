from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

console = Console(stderr=True)


class DownloadError(RuntimeError):
    pass


def make_client(cfg: dict[str, Any]) -> httpx.Client:
    timeout = httpx.Timeout(cfg.get("request_timeout_seconds", 120))
    headers = {"User-Agent": cfg.get("user_agent", "casehistory/0.1")}
    return httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException, DownloadError)),
)
def download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    force: bool = False,
    expected_min_bytes: int = 0,
) -> dict[str, Any]:
    """Idempotent download. Skips when dest exists and is non-empty unless force."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > expected_min_bytes and not force:
        return {
            "url": url,
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "sha256": sha256_file(dest),
            "skipped": True,
            "status": "exists",
        }

    partial = dest.with_suffix(dest.suffix + ".partial")
    etag = None
    last_modified = None
    with client.stream("GET", url) as resp:
        if resp.status_code >= 400:
            raise DownloadError(f"HTTP {resp.status_code} for {url}")
        etag = resp.headers.get("etag")
        last_modified = resp.headers.get("last-modified")
        written = 0
        with partial.open("wb") as out:
            for chunk in resp.iter_bytes(1024 * 256):
                out.write(chunk)
                written += len(chunk)
        if written < expected_min_bytes:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"Too small ({written} bytes) for {url}")

    partial.replace(dest)
    digest = sha256_file(dest)
    return {
        "url": url,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": digest,
        "skipped": False,
        "status": "downloaded",
        "etag": etag,
        "last_modified": last_modified,
    }


def get_text(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    if resp.status_code >= 400:
        raise DownloadError(f"HTTP {resp.status_code} for {url}")
    return resp.text


def get_json(client: httpx.Client, url: str) -> Any:
    resp = client.get(url)
    if resp.status_code >= 400:
        raise DownloadError(f"HTTP {resp.status_code} for {url}")
    return resp.json()


def polite_sleep(seconds: float = 0.05) -> None:
    if seconds > 0:
        time.sleep(seconds)
