"""Workable job boards.

``https://apply.workable.com/api/v1/widget/accounts/{org}?details=true``

Two things to know. First, Workable exposes ``telecommuting``, ``country`` and
``countryCode``, which together are a clean remote-plus-geography signal.
Second, and more important: some Workable postings are **talent pools**, not
live requisitions — they say "active pooling purposes only… does not guarantee
employment". Those are flagged via :func:`scout.filters.is_talent_pool`, never
dropped.

Status note: on 2026-10-15 every org slug tried returned HTTP 200 with an empty
``jobs`` array, so this source ships disabled in ``config.yaml``. The parser is
implemented and fixture-tested; re-enable it once a known-good slug is found.
"""

from __future__ import annotations

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp, strip_html

LIST_URL = "https://apply.workable.com/api/v1/widget/accounts/{org}?details=true"
DETAIL_URL = "https://apply.workable.com/api/v1/accounts/{org}/jobs/{shortcode}"


def parse(payload: dict, org: str) -> list[Job]:
    """Normalise one Workable account payload into jobs."""
    company = (payload or {}).get("name") or org
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        locations: list[str] = []
        for entry in item.get("locations") or []:
            if isinstance(entry, dict):
                parts = [entry.get("city"), entry.get("region"), entry.get("country")]
                joined = ", ".join(p for p in parts if p)
                if joined:
                    locations.append(joined)
            elif entry:
                locations.append(str(entry))
        if not locations:
            fallback = ", ".join(
                str(p) for p in [item.get("city"), item.get("country")] if p
            )
            if fallback:
                locations.append(fallback)
        if item.get("telecommuting"):
            locations.append("Remote")
        jobs.append(
            Job(
                source="workable",
                org=org,
                external_id=str(item.get("shortcode") or item.get("id") or ""),
                title=item.get("title") or "",
                company=company,
                url=item.get("url") or item.get("application_url") or "",
                apply_url=item.get("application_url") or item.get("url") or "",
                locations=locations,
                remote_flag=item.get("telecommuting"),
                department=item.get("department"),
                description=strip_html(item.get("description")),
                posted_at=parse_timestamp(item.get("published_on") or item.get("created_at")),
            )
        )
    return jobs


def fetch(client: PoliteClient, config: Config) -> list[Job]:
    """Poll every configured Workable org."""
    jobs: list[Job] = []
    for org in config.source("workable").get("orgs") or []:
        payload = client.get_json(LIST_URL.format(org=org))
        if payload:
            jobs.extend(parse(payload, org))
    return jobs
