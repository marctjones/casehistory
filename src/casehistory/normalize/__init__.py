from __future__ import annotations

from typing import Any

from casehistory.normalize import cap_cases, uscode_sections


def run_all(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ncfg = cfg.get("normalize") or {}
    if ncfg.get("uscode_sections", True):
        out["uscode_sections"] = uscode_sections.normalize(cfg)
    if ncfg.get("cap_cases", True):
        out["cap_cases"] = cap_cases.normalize(cfg)
    return out
