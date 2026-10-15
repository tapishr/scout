"""SmartRecruiters postings.

``https://api.smartrecruiters.com/v1/companies/{org}/postings``

Status note: on 2026-10-15 every company identifier tried returned HTTP 200 with
``totalFound: 0``, so this source ships disabled in ``config.yaml``. The parser
is implemented and fixture-tested against the documented response shape.
"""

from __future__ import annotations

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp

URL = "https://api.smartrecruiters.com/v1/companies/{org}/postings?limit=100"


def parse(payload: dict, org: str) -> list[Job]:
    """Normalise one SmartRecruiters payload into jobs."""
    jobs: list[Job] = []
    for item in (payload or {}).get("content") or []:
        location = item.get("location") or {}
        parts = [location.get("city"), location.get("region"), location.get("country")]
        label = ", ".join(str(p) for p in parts if p)
        locations = [label] if label else []
        if location.get("remote"):
            locations.append("Remote")
        jobs.append(
            Job(
                source="smartrecruiters",
                org=org,
                external_id=str(item.get("id") or ""),
                title=item.get("name") or "",
                company=(item.get("company") or {}).get("name") or org,
                url=item.get("ref") or item.get("applyUrl") or "",
                apply_url=item.get("applyUrl") or item.get("ref") or "",
                locations=locations,
                remote_flag=location.get("remote"),
                department=(item.get("department") or {}).get("label"),
                posted_at=parse_timestamp(item.get("releasedDate")),
            )
        )
    return jobs


def fetch(client: PoliteClient, config: Config) -> list[Job]:
    """Poll every configured SmartRecruiters company."""
    jobs: list[Job] = []
    for org in config.source("smartrecruiters").get("orgs") or []:
        payload = client.get_json(URL.format(org=org))
        if payload:
            jobs.extend(parse(payload, org))
    return jobs
