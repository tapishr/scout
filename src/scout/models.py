"""Core data shapes.

Every fetcher normalises its source's payload into a :class:`Job`. Everything
downstream — filtering, diffing, rendering — speaks only this shape, so adding a
new job board never touches the filter or digest code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Bucket(str, Enum):
    """India-eligibility classification.

    The distinction between the first two is the product. ``REMOTE_INDIA_ONLY``
    has nothing left to renegotiate; ``REMOTE_INDIA_OR_OFFICE`` leaves the
    employer a lever to pull you onsite later, which changes how you read an
    offer. ``SUSPICIOUS`` exists so that an unclassifiable posting is surfaced
    for human reading rather than silently dropped — dropping it would
    re-introduce the exact failure this tool was built to remove.
    """

    REMOTE_INDIA_ONLY = "remote-india-only"
    REMOTE_INDIA_OR_OFFICE = "remote-india-or-office"
    SUSPICIOUS = "suspicious"
    EXCLUDED = "excluded"


#: Order in which buckets are rendered. Best news first.
BUCKET_ORDER = [
    Bucket.REMOTE_INDIA_ONLY,
    Bucket.REMOTE_INDIA_OR_OFFICE,
    Bucket.SUSPICIOUS,
    Bucket.EXCLUDED,
]


@dataclass
class Job:
    """One posting, normalised across sources.

    ``raw`` is kept only for the lifetime of a run (debugging a parser) and is
    deliberately *not* persisted — snapshots stay small and diffable.
    """

    source: str
    org: str
    external_id: str
    title: str
    company: str
    url: str
    apply_url: str = ""
    locations: list[str] = field(default_factory=list)
    remote_flag: bool | None = None
    compensation: str | None = None
    department: str | None = None
    description: str = ""
    #: Explicit geographic eligibility, where a source states it (Himalayas
    #: ``locationRestrictions``, Remotive ``candidate_required_location``).
    location_restrictions: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Stable identity used for diffing between runs.

        Deliberately excludes mutable fields (compensation, ``updated_at``) so a
        posting that is merely edited is not re-reported as new. Falls back to a
        content hash when a source exposes no stable id.
        """
        ident = self.external_id or hashlib.sha1(
            "|".join([self.source, self.org, self.title, ";".join(sorted(self.locations))]).encode()
        ).hexdigest()[:16]
        return f"{self.source}:{self.org}:{ident}"

    @property
    def location_blob(self) -> str:
        """All geography-bearing text, lowercased, for keyword classification."""
        parts = [*self.locations, *self.location_restrictions]
        return " | ".join(p for p in parts if p).lower()

    def age_days(self, now: datetime | None = None) -> int | None:
        """Days since posting, or ``None`` when the source gives no date."""
        stamp = self.posted_at or self.updated_at
        if stamp is None:
            return None
        now = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, (now - stamp).days)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for a snapshot. ``raw`` is dropped on purpose."""
        return {
            "source": self.source,
            "org": self.org,
            "external_id": self.external_id,
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "apply_url": self.apply_url,
            "locations": self.locations,
            "remote_flag": self.remote_flag,
            "compensation": self.compensation,
            "department": self.department,
            "location_restrictions": self.location_restrictions,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        """Rehydrate from a snapshot, tolerating keys written by older versions."""

        def _dt(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return cls(
            source=data.get("source", ""),
            org=data.get("org", ""),
            external_id=data.get("external_id", ""),
            title=data.get("title", ""),
            company=data.get("company", ""),
            url=data.get("url", ""),
            apply_url=data.get("apply_url", ""),
            locations=list(data.get("locations") or []),
            remote_flag=data.get("remote_flag"),
            compensation=data.get("compensation"),
            department=data.get("department"),
            location_restrictions=list(data.get("location_restrictions") or []),
            posted_at=_dt(data.get("posted_at")),
            updated_at=_dt(data.get("updated_at")),
        )


@dataclass
class Classification:
    """Why a job landed in the bucket it did.

    ``reasons`` is carried so the digest can explain itself. A classifier the
    reader cannot audit is a classifier the reader will not trust.
    """

    bucket: Bucket
    seniority_match: bool
    role_match: bool
    is_talent_pool: bool = False
    reasons: list[str] = field(default_factory=list)
    #: Read-order within a bucket; lower is read first. Lets the digest put a
    #: genuinely ambiguous "Remote" above one explicitly restricted to the US.
    review_priority: int = 50


@dataclass
class ScoutedJob:
    """A job paired with its classification."""

    job: Job
    classification: Classification

    @property
    def key(self) -> str:
        return self.job.key


@dataclass
class SourceResult:
    """Outcome of polling one source.

    A source that raised is recorded rather than propagated: one dead API must
    not kill the run. ``ok`` also gates disappearance detection — see
    :func:`scout.store.diff`.
    """

    source: str
    jobs: list[Job] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Snapshot:
    """One run's full result, as persisted."""

    run_at: datetime
    jobs: list[Job] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "run_at": self.run_at.isoformat(),
            "sources_ok": self.sources_ok,
            "sources_failed": self.sources_failed,
            "jobs": [j.to_dict() for j in self.jobs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        run_at = datetime.fromisoformat(data["run_at"])
        return cls(
            run_at=run_at,
            jobs=[Job.from_dict(j) for j in data.get("jobs", [])],
            sources_ok=list(data.get("sources_ok") or []),
            sources_failed=dict(data.get("sources_failed") or {}),
        )


@dataclass
class DiffResult:
    """What changed since the previous run."""

    new: list[ScoutedJob] = field(default_factory=list)
    disappeared: list[Job] = field(default_factory=list)
    carried_over: list[ScoutedJob] = field(default_factory=list)
    #: Sources skipped for disappearance detection because they failed this run.
    skipped_sources: list[str] = field(default_factory=list)
    is_first_run: bool = False
