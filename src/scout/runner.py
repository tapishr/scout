"""Run orchestration: poll, classify, diff.

Kept separate from :mod:`scout.cli` so a run can be driven from a test or
another program without going through argument parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config
from .fetchers import SOURCES
from .filters import classify_all
from .http import PoliteClient
from .models import Bucket, DiffResult, Job, ScoutedJob, SourceResult
from .store import SnapshotStore, build_snapshot, diff

log = logging.getLogger(__name__)


@dataclass
class RunOutcome:
    """Everything one run produced, for the CLI to render.

    A dataclass rather than a tuple because the call site reads better and new
    fields (``dead_urls`` was one) do not break unpacking at every caller.
    """

    diff: DiffResult
    reported: list[ScoutedJob]
    sources_ok: list[str]
    sources_failed: dict[str, str]
    dead_urls: list[str] = field(default_factory=list)

#: Buckets kept in the snapshot and reported. ``EXCLUDED`` jobs are dropped from
#: the snapshot: persisting tens of thousands of irrelevant postings would bloat
#: every future diff for no gain.
REPORTABLE = {Bucket.REMOTE_INDIA_ONLY, Bucket.REMOTE_INDIA_OR_OFFICE, Bucket.SUSPICIOUS}


def poll_sources(
    config: Config,
    client: PoliteClient,
    only: list[str] | None = None,
) -> list[SourceResult]:
    """Poll every enabled source, isolating failures.

    One dead API must never kill the run, so each fetcher is wrapped: an
    exception becomes a recorded :class:`SourceResult` error and the run
    continues with whatever succeeded.
    """
    results: list[SourceResult] = []
    for name in config.enabled_sources:
        if only and name not in only:
            continue
        fetch = SOURCES.get(name)
        if fetch is None:
            results.append(SourceResult(source=name, error="no fetcher registered"))
            continue
        try:
            jobs = fetch(client, config)
        except Exception as exc:  # noqa: BLE001 — containment is the point
            log.warning("source %s failed: %s", name, exc)
            results.append(SourceResult(source=name, error=f"{type(exc).__name__}: {exc}"))
        else:
            log.info("source %s returned %d jobs", name, len(jobs))
            results.append(SourceResult(source=name, jobs=jobs))
    return results


def run(
    config: Config,
    store: SnapshotStore,
    *,
    only: list[str] | None = None,
    report_all: bool = False,
    show_excluded: bool = False,
    dry_run: bool = False,
    client: PoliteClient | None = None,
) -> RunOutcome:
    """Execute one full run."""
    client = client or PoliteClient(config.http)
    results = poll_sources(config, client, only=only)

    sources_ok = [r.source for r in results if r.ok]
    sources_failed = {r.source: r.error or "unknown error" for r in results if not r.ok}

    raw_jobs: list[Job] = [job for result in results if result.ok for job in result.jobs]
    classified = classify_all(raw_jobs, config)

    keep = set(REPORTABLE)
    if show_excluded:
        keep.add(Bucket.EXCLUDED)
    reportable = [s for s in classified if s.classification.bucket in keep]

    # Deduplicate: aggregators routinely surface the same posting as a company
    # board. First occurrence wins, and sources are polled in config order, so
    # curated boards (richer data) take precedence over aggregators.
    seen: set[str] = set()
    deduped: list[ScoutedJob] = []
    for scouted in reportable:
        if scouted.key in seen:
            continue
        seen.add(scouted.key)
        deduped.append(scouted)

    previous = store.load_latest()
    if report_all:
        result = DiffResult(new=deduped, disappeared=[], carried_over=[], is_first_run=True)
    else:
        result = diff(previous, deduped, sources_ok)

    if not dry_run and not report_all:
        snapshot = build_snapshot([s.job for s in deduped], sources_ok, sources_failed)
        store.save(snapshot)

    return RunOutcome(
        diff=result,
        reported=deduped,
        sources_ok=sources_ok,
        sources_failed=sources_failed,
        dead_urls=list(getattr(client, "dead_urls", [])),
    )
