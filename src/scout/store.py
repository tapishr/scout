"""Snapshot persistence and run-to-run diffing.

Storage is a directory of timestamped JSON files rather than a database. The
access pattern is a whole-file comparison of two consecutive runs, not a query;
the data is a few MB; and being able to ``jq`` yesterday's snapshot by hand is a
real feature for a tool whose job is to be trusted. See CONTEXT.md §5.2 for the
full justification and the conditions under which SQLite would win.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import DiffResult, Job, ScoutedJob, Snapshot

#: Override with --state-dir or the SCOUT_STATE_DIR environment variable.
DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "scout"
RUNS_DIRNAME = "runs"


def resolve_state_dir(explicit: str | Path | None = None) -> Path:
    """Pick the state directory: explicit flag, then env var, then default."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get("SCOUT_STATE_DIR")
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_STATE_DIR


class SnapshotStore:
    """Reads and writes run snapshots under a state directory."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.state_dir = resolve_state_dir(state_dir)
        self.runs_dir = self.state_dir / RUNS_DIRNAME

    def _ensure(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_paths(self) -> list[Path]:
        """All snapshot files, oldest first."""
        if not self.runs_dir.is_dir():
            return []
        return sorted(self.runs_dir.glob("*.json"))

    def load_latest(self) -> Snapshot | None:
        """Most recent snapshot, or ``None`` on a first run.

        A corrupt or unreadable snapshot is treated as absent rather than fatal:
        the cost is one over-full digest, which is strictly better than a tool
        that refuses to run.
        """
        for path in reversed(self.snapshot_paths()):
            try:
                with open(path, encoding="utf-8") as handle:
                    return Snapshot.from_dict(json.load(handle))
            except (OSError, ValueError, KeyError):
                continue
        return None

    def save(self, snapshot: Snapshot) -> Path:
        """Persist a snapshot, named by its UTC run timestamp."""
        self._ensure()
        stamp = snapshot.run_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.runs_dir / f"{stamp}.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, indent=2, ensure_ascii=False)
        return path

    def prune(self, keep: int = 60) -> int:
        """Delete all but the newest ``keep`` snapshots. Returns how many went."""
        paths = self.snapshot_paths()
        doomed = paths[:-keep] if keep > 0 else []
        for path in doomed:
            try:
                path.unlink()
            except OSError:
                pass
        return len(doomed)


def diff(
    previous: Snapshot | None,
    current: list[ScoutedJob],
    sources_ok: list[str],
) -> DiffResult:
    """Compare this run against the previous one.

    A disappeared requisition is signal worth reporting — pulled reqs are a real
    outcome a candidate needs to know about.

    The correctness trap this function exists to avoid: **a source that failed
    this run must not have its jobs reported as disappeared.** One flaky API
    would otherwise produce a digest claiming hundreds of roles were pulled.
    Disappearance is therefore computed only for sources that actually returned
    data, and the suppressed sources are named in the result.
    """
    if previous is None:
        return DiffResult(new=list(current), disappeared=[], carried_over=[], is_first_run=True)

    previous_by_key: dict[str, Job] = {job.key: job for job in previous.jobs}
    current_keys = {scouted.key for scouted in current}
    ok = set(sources_ok)

    new = [scouted for scouted in current if scouted.key not in previous_by_key]
    carried_over = [scouted for scouted in current if scouted.key in previous_by_key]

    missing = [job for key, job in previous_by_key.items() if key not in current_keys]
    disappeared = [job for job in missing if job.source in ok]
    skipped = sorted({job.source for job in missing if job.source not in ok})

    return DiffResult(
        new=new,
        disappeared=disappeared,
        carried_over=carried_over,
        skipped_sources=skipped,
        is_first_run=False,
    )


def build_snapshot(
    jobs: list[Job], sources_ok: list[str], sources_failed: dict[str, str]
) -> Snapshot:
    """Assemble a snapshot for the current run."""
    return Snapshot(
        run_at=datetime.now(timezone.utc),
        jobs=jobs,
        sources_ok=sources_ok,
        sources_failed=sources_failed,
    )
