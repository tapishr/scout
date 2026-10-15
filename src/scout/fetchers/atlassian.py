"""Atlassian's bespoke careers endpoint.

``https://www.atlassian.com/endpoint/careers/listings`` returns a bare array.

The highest-signal source in the tool: its location strings state the
remote-versus-office distinction explicitly, in forms like
``"Remote - India - Remote"`` versus ``"Bengaluru - India - Bengaluru, 560071
India"``. Requisition 25283 ("Senior Infrastructure Engineer") is the canonical
``remote-india-only`` posting and is the reference case in the filter tests.
"""

from __future__ import annotations

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp, strip_html

URL = "https://www.atlassian.com/endpoint/careers/listings"


def parse(payload: list) -> list[Job]:
    """Normalise the Atlassian listings array into jobs."""
    jobs: list[Job] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        portal = item.get("portalJobPost") or {}
        description = " ".join(
            strip_html(item.get(section), limit=800)
            for section in ("overview", "responsibilities", "qualifications")
        ).strip()
        jobs.append(
            Job(
                source="atlassian",
                org="atlassian",
                external_id=str(item.get("id") or ""),
                title=item.get("title") or "",
                company="Atlassian",
                url=portal.get("portalUrl") or item.get("applyUrl") or "",
                apply_url=item.get("applyUrl") or portal.get("portalUrl") or "",
                locations=[str(loc) for loc in (item.get("locations") or []) if loc],
                department=item.get("category"),
                description=description,
                updated_at=parse_timestamp(portal.get("updatedDate")),
            )
        )
    return jobs


def fetch(client: PoliteClient, config: Config) -> list[Job]:
    """Poll Atlassian. No org list — it is a single company endpoint."""
    payload = client.get_json(URL)
    return parse(payload if isinstance(payload, list) else [])
