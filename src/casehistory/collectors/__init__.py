from __future__ import annotations

from typing import Any, Callable

from casehistory.collectors import cap, courtlistener, scdb, uscode

CollectorFn = Callable[..., dict[str, Any]]

COLLECTORS: dict[str, CollectorFn] = {
    "uscode": uscode.collect,
    "cap_us_reports": cap.collect,
    "scdb": scdb.collect,
    "courtlistener": courtlistener.collect,
}
