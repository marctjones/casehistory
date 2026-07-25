from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
import yaml
from rich.console import Console
from rich.table import Table

from casehistory.collectors import COLLECTORS
from casehistory.normalize import run_all as normalize_all
from casehistory.paths import data_root, repo_root, reports_dir

console = Console()


def load_config(path: Path | None) -> dict[str, Any]:
    cfg_path = path or (repo_root() / "config.yaml")
    with cfg_path.open() as f:
        return yaml.safe_load(f)


def cmd_collect(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config) if args.config else None)
    data_root(cfg).mkdir(parents=True, exist_ok=True)

    names = args.sources or list(COLLECTORS.keys())
    unknown = [n for n in names if n not in COLLECTORS]
    if unknown:
        console.print(f"[red]Unknown sources: {unknown}. Known: {list(COLLECTORS)}")
        return 2

    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "sources": {},
    }
    for name in names:
        console.rule(f"[bold]Collect {name}")
        result = COLLECTORS[name](cfg, force=args.force)
        summary["sources"][name] = result
        console.print(result)

    if args.normalize:
        console.rule("[bold]Normalize")
        summary["normalize"] = normalize_all(cfg)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    rdir = reports_dir(cfg)
    rdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = rdir / f"collect_{stamp}.json"
    report_path.write_bytes(orjson.dumps(summary, option=orjson.OPT_INDENT_2))
    (rdir / "latest.json").write_bytes(orjson.dumps(summary, option=orjson.OPT_INDENT_2))
    console.print(f"[green]Report:[/] {report_path}")
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config) if args.config else None)
    console.rule("[bold]Normalize")
    result = normalize_all(cfg)
    console.print(result)
    rdir = reports_dir(cfg)
    rdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = rdir / f"normalize_{stamp}.json"
    path.write_bytes(orjson.dumps(result, option=orjson.OPT_INDENT_2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config) if args.config else None)
    root = data_root(cfg)
    table = Table(title=f"Data under {root}")
    table.add_column("Path")
    table.add_column("Files", justify="right")
    table.add_column("Bytes", justify="right")

    if not root.exists():
        console.print("[yellow]No data directory yet. Run: casehistory collect")
        return 0

    for sub in sorted(root.rglob("*")):
        if sub.is_dir():
            files = [p for p in sub.iterdir() if p.is_file()]
            if not files:
                continue
            # only show leaf-ish dirs with files
            if any(p.is_dir() for p in sub.iterdir()):
                continue
            total = sum(p.stat().st_size for p in files)
            table.add_row(str(sub.relative_to(root)), str(len(files)), f"{total:,}")

    console.print(table)
    latest = reports_dir(cfg) / "latest.json"
    if latest.exists():
        console.print(orjson.loads(latest.read_bytes()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="casehistory",
        description="Collect SCOTUS cases + U.S. Code versions for historical exploration",
    )
    p.add_argument("--config", help="Path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="Download a snapshot from configured sources")
    c.add_argument(
        "--sources",
        nargs="+",
        choices=list(COLLECTORS.keys()),
        help="Subset of sources (default: all enabled)",
    )
    c.add_argument("--force", action="store_true", help="Re-download even if present")
    c.add_argument("--normalize", action="store_true", help="Run normalize after collect")
    c.set_defaults(func=cmd_collect)

    n = sub.add_parser("normalize", help="Build normalized jsonl from raw downloads")
    n.set_defaults(func=cmd_normalize)

    s = sub.add_parser("status", help="Show local snapshot inventory")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
