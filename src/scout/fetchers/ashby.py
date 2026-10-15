"""Ashby job boards.

``https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true``

Ashby's list endpoint uniquely includes ``descriptionPlain``, so job description
text is available without an N+1 detail fetch — which is why talent-pool
detection works well here and not on Greenhouse.
"""

from __future__ import annotations

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp

BASE = "https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"


def _compensation(payload: dict) -> str | None:
    comp = payload.get("compensation") or {}
    for key in ("compensationTierSummary", "scrapeableCompensationSalarySummary"):
        value = comp.get(key)
        if value:
            return str(value)
    return None


def parse(payload: dict, org: str) -> list[Job]:
    """Normalise one Ashby board payload into jobs."""
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        if item.get("isListed") is False:
            continue
        locations = [item.get("location")] if item.get("location") else []
        locations += [
            secondary.get("location")
            for secondary in (item.get("secondaryLocations") or [])
            if secondary.get("location")
        ]
        jobs.append(
            Job(
                source="ashby",
                org=org,
                external_id=str(item.get("id") or ""),
                title=item.get("title") or "",
                company=org,
                url=item.get("jobUrl") or "",
                apply_url=item.get("applyUrl") or item.get("jobUrl") or "",
                locations=[loc for loc in locations if loc],
                remote_flag=item.get("isRemote"),
                compensation=_compensation(item),
                department=item.get("department") or item.get("team"),
                description=(item.get("descriptionPlain") or "")[:2000],
                posted_at=parse_timestamp(item.get("publishedAt")),
            )
        )
    return jobs


def fetch(client: PoliteClient, config: Config) -> list[Job]:
    """Poll every configured Ashby org. A 404 org is skipped, not fatal."""
    jobs: list[Job] = []
    for org in config.source("ashby").get("orgs") or []:
        payload = client.get_json(BASE.format(org=org))
        if payload:
            jobs.extend(parse(payload, org))
    return jobs
