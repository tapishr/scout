"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from .config import ConfigError, load_config
from .digest import render
from .fetchers import SOURCES
from .store import SnapshotStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout",
        description="Find India-eligible remote senior engineering roles and report what is new.",
    )
    parser.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument(
        "--state-dir", help="where run snapshots live (default: ~/.local/share/scout)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log each source as it is polled"
    )
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="poll sources, diff against the last run, print a digest")
    run_cmd.add_argument(
        "--source",
        action="append",
        choices=sorted(SOURCES),
        help="restrict to one source (repeatable)",
    )
    run_cmd.add_argument(
        "--all", action="store_true", help="report everything matching, not just new"
    )
    run_cmd.add_argument("--show-excluded", action="store_true", help="include filtered-out roles")
    run_cmd.add_argument("--dry-run", action="store_true", help="do not write a snapshot")
    run_cmd.add_argument("-o", "--output", help="write the digest to a file instead of stdout")

    sub.add_parser("history", help="list stored run snapshots")
    prune_cmd = sub.add_parser("prune", help="delete old snapshots")
    prune_cmd.add_argument("--keep", type=int, default=60, help="how many to keep (default: 60)")
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from .runner import run as execute_run

    config = load_config(args.config)
    store = SnapshotStore(args.state_dir)
    outcome = execute_run(
        config,
        store,
        only=args.source,
        report_all=args.all,
        show_excluded=args.show_excluded,
        dry_run=args.dry_run,
    )
    digest = render(
        outcome.diff,
        config,
        sources_ok=outcome.sources_ok,
        sources_failed=outcome.sources_failed,
        total_matched=len(outcome.reported),
        dead_urls=outcome.dead_urls,
        show_excluded=args.show_excluded,
        now=datetime.now(timezone.utc),
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(digest)
        print(
            f"wrote {args.output} ({len(outcome.diff.new)} new, "
            f"{len(outcome.diff.disappeared)} disappeared)"
        )
    else:
        print(digest)
    # A run where every source failed is a failed run, and should wake a cron job.
    return 1 if outcome.sources_failed and not outcome.sources_ok else 0


def _cmd_history(args: argparse.Namespace) -> int:
    store = SnapshotStore(args.state_dir)
    paths = store.snapshot_paths()
    if not paths:
        print(f"no snapshots yet in {store.runs_dir}")
        return 0
    print(f"{len(paths)} snapshot(s) in {store.runs_dir}:")
    for path in paths:
        print(f"  {path.name}  ({path.stat().st_size / 1024:.1f} KiB)")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    store = SnapshotStore(args.state_dir)
    removed = store.prune(keep=args.keep)
    print(f"removed {removed} snapshot(s), kept up to {args.keep}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {"run": _cmd_run, "history": _cmd_history, "prune": _cmd_prune}
    handler = handlers.get(args.command or "run")
    if handler is None:
        build_parser().print_help()
        return 2
    try:
        return handler(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
