# casehistory

Collect **historical U.S. Supreme Court case text** and **versioned U.S. Code** in machine-readable form, normalize them for exploration (code change-over-time, opinion text, citations / connections).

No API keys. Collection is **on-demand and re-runnable** (not scheduled).

## Snapshot already on disk

A first snapshot was collected under `data/` (gitignored). Summary: `data/reports/snapshot_summary.json`.

| Dataset | What | Scale (this snapshot) |
|---------|------|------------------------|
| **CAP U.S. Reports** | Case JSON + full opinion text | 572 volumes, **329,995** cases → `normalized/scotus/cap_cases.jsonl` (~1 GB) |
| **OLRC U.S. Code** | USLM XML release points + annual zips | **41** full-code XML versions + 31 annual archives → `normalized/uscode/sections.jsonl` (~14 GB) + index (~500 MB) |
| **SCDB** | Coded SCOTUS metadata (modern + legacy) | 5 CSV zips in `raw/scdb/` |
| **CourtListener** | Recent SCOTUS search metadata / PDF links | 2,000 rows (100 pages) in `raw/courtlistener/scotus_search.jsonl` |

**Code change demo** (index hashes, versions `118-150` → `119-99`): **2,768** sections changed text, **630** added, **160** removed, **57,060** unchanged.

## Quick start

```bash
uv sync
uv run casehistory collect --normalize   # refresh / extend snapshot
uv run casehistory status
uv run casehistory normalize             # rebuild jsonl from raw only
```

Re-runs are **idempotent** (skip existing files). Use `--force` to re-download.

```bash
uv run casehistory collect --sources uscode
uv run casehistory collect --sources cap_us_reports scdb
uv run casehistory collect --force --normalize
```

## Config (`config.yaml`)

| Knob | Meaning |
|------|---------|
| `sources.uscode.max_release_points` | `40` now; set **`null`** for all ~380 Pub.L. snapshots (large) |
| `sources.cap_us_reports.volume_range` | e.g. `[1, 50]` for partial CAP |
| `sources.courtlistener.max_pages` | Cap search crawl; `null` for full CL metadata (rate-limited) |
| `concurrency` | Parallel downloads |

## Layout

```
config.yaml
src/casehistory/
  collectors/     # uscode, cap, scdb, courtlistener
  normalize/      # raw → jsonl for a future site
  cli.py
data/             # gitignored
  raw/
  normalized/
  manifest.sqlite
  reports/
```

## Toward the exploration website

1. **Code over time** — section rows keyed by `(identifier, version_id)` with `text_sha256_16` for cheap diffs; full `text` for display.
2. **Opinions** — CAP is the historical full-text backbone; CourtListener adds modern slip PDFs / metadata (expand `max_pages` or set `null` and re-run).
3. **Connections** — join SCDB legal provisions + CAP `cites_to` + future regex of `U.S.C.` citations in opinion text against code identifiers.
4. **Re-collection** — same CLI whenever you want a fresher snapshot; no cron required.

## Sources (no keys)

- [OLRC downloads](https://uscode.house.gov/download/download.shtml) — USLM XML
- [CAP static bulk](https://static.case.law/us/) — U.S. Reports
- [SCDB](http://scdb.wustl.edu/) — Spaeth database CSVs
- [CourtListener search API](https://www.courtlistener.com/api/rest/v4/search/) + public S3 bulk courts table

## License / terms

Federal judicial opinions and U.S. Code text are generally free of copyright as government works. Prefer bulk endpoints; respect rate limits (CourtListener will 429 if you go too fast — the collector backs off and can resume).
