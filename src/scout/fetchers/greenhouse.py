"""Greenhouse job boards.

``https://boards-api.greenhouse.io/v1/boards/{org}/jobs``

The list endpoint carries no description; ``content`` only comes from the
per-job detail endpoint. Fetching detail for every posting is N+1 requests
against a free API, so it is opt-in per org via ``fetch_details``/``detail_orgs``.
"""

from __future__ import annotations

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp, split_locations, strip_html

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{org}/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}"


def parse(payload: dict, org: str) -> list[Job]:
    """Normalise one Greenhouse board payload into jobs."""
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        location_name = ((item.get("location") or {}) or {}).get("name") or ""
        jobs.append(
            Job(
                source="greenhouse",
                org=org,
                external_id=str(item.get("id") or ""),
                title=item.get("title") or "",
                company=item.get("company_name") or org,
                url=item.get("absolute_url") or "",
                apply_url=item.get("absolute_url") or "",
                locations=split_locations(location_name),
                updated_at=parse_timestamp(item.get("updated_at")),
                posted_at=parse_timestamp(item.get("first_published")),
            )
        )
    return jobs


def fetch(client: PoliteClient, config: Config) -> list[Job]:
    """Poll every configured Greenhouse org, optionally enriching with detail."""
    settings = config.source("greenhouse")
    detail_orgs = set(settings.get("detail_orgs") or [])
    fetch_details = bool(settings.get("fetch_details"))

    jobs: list[Job] = []
    for org in settings.get("orgs") or []:
        payload = client.get_json(LIST_URL.format(org=org))
        if not payload:
            continue
        parsed = parse(payload, org)
        if fetch_details and org in detail_orgs:
            for job in parsed:
                detail = client.get_json(DETAIL_URL.format(org=org, job_id=job.external_id))
                if detail:
                    job.description = strip_html(detail.get("content"))
        jobs.extend(parsed)
    return jobs
