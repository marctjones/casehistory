from __future__ import annotations

from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_root(cfg: dict[str, Any]) -> Path:
    raw = cfg.get("data_dir", "data")
    p = Path(raw)
    if not p.is_absolute():
        p = repo_root() / p
    return p


def raw_dir(cfg: dict[str, Any], *parts: str) -> Path:
    return data_root(cfg).joinpath("raw", *parts)


def normalized_dir(cfg: dict[str, Any], *parts: str) -> Path:
    return data_root(cfg).joinpath("normalized", *parts)


def reports_dir(cfg: dict[str, Any]) -> Path:
    return data_root(cfg) / "reports"
