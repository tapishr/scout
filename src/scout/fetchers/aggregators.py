"""Remote-job aggregators.

These exist to **discover companies not on the curated board lists**. Their data
is shallower than a company's own board, but several expose an explicit
geographic-eligibility field, which is the single most valuable signal in the
tool:

====================  ==========================================================
Himalayas             ``locationRestrictions`` — the best India signal anywhere
Remotive              ``candidate_required_location``
Jobicy                ``jobGeo``
RemoteOK              ``location`` only (weaker); first array element is metadata
WorkingNomads         ``location`` only (weaker)
====================  ==========================================================

Where a source states restrictions explicitly, they are mapped to
:attr:`Job.location_restrictions` rather than :attr:`Job.locations`, because the
classifier treats an explicit restriction as authoritative.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ._util import parse_timestamp, split_locations, strip_html

HIMALAYAS_URL = "https://himalayas.app/jobs/api?limit={limit}"
#: Himalayas caps a page at 20 regardless of ``limit``, so volume needs the cursor.
HIMALAYAS_PAGE_SIZE = 20
REMOTEOK_URL = "https://remoteok.com/api"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit={limit}"
WORKINGNOMADS_URL = "https://www.workingnomads.com/api/exposed_jobs/"
JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs?count={count}"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    """Stable org slug from a company name, so diff keys survive cosmetic edits."""
    return _SLUG_RE.sub("-", (value or "unknown").lower()).strip("-") or "unknown"


def _salary(minimum: object, maximum: object, currency: object = None) -> str | None:
    """Render a salary range, tolerating either bound being absent."""
    low = minimum if isinstance(minimum, (int, float)) and minimum else None
    high = maximum if isinstance(maximum, (int, float)) and maximum else None
    if not low and not high:
        return None
    unit = f" {currency}" if currency else ""
    if low and high:
        return f"{low:,.0f}–{high:,.0f}{unit}"
    return f"{(low or high):,.0f}{unit}"


def parse_himalayas(payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        company = item.get("companyName") or ""
        restrictions = [str(r) for r in (item.get("locationRestrictions") or []) if r]
        jobs.append(
            Job(
                source="himalayas",
                org=item.get("companySlug") or _slug(company),
                external_id=str(item.get("guid") or ""),
                title=item.get("title") or "",
                company=company,
                url=item.get("applicationLink") or "",
                apply_url=item.get("applicationLink") or "",
                locations=["Remote"],
                location_restrictions=restrictions,
                remote_flag=True,
                compensation=_salary(
                    item.get("minSalary"), item.get("maxSalary"), item.get("currency")
                ),
                description=strip_html(item.get("excerpt") or item.get("description")),
                posted_at=parse_timestamp(item.get("pubDate")),
            )
        )
    return jobs


def parse_remoteok(payload: list) -> list[Job]:
    """Parse RemoteOK. The first array element is metadata and is skipped."""
    jobs: list[Job] = []
    for item in (payload or [])[1:]:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        company = item.get("company") or ""
        jobs.append(
            Job(
                source="remoteok",
                org=_slug(company),
                external_id=str(item.get("id") or item.get("slug") or ""),
                title=item.get("position") or "",
                company=company,
                url=item.get("url") or "",
                apply_url=item.get("apply_url") or item.get("url") or "",
                locations=split_locations(item.get("location")) or ["Remote"],
                remote_flag=True,
                compensation=_salary(item.get("salary_min"), item.get("salary_max"), "USD"),
                description=strip_html(item.get("description")),
                posted_at=parse_timestamp(item.get("epoch") or item.get("date")),
            )
        )
    return jobs


def parse_remotive(payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        company = item.get("company_name") or ""
        required = item.get("candidate_required_location") or ""
        jobs.append(
            Job(
                source="remotive",
                org=_slug(company),
                external_id=str(item.get("id") or ""),
                title=item.get("title") or "",
                company=company,
                url=item.get("url") or "",
                apply_url=item.get("url") or "",
                locations=["Remote"],
                location_restrictions=split_locations(required),
                remote_flag=True,
                compensation=item.get("salary") or None,
                department=item.get("category"),
                description=strip_html(item.get("description")),
                posted_at=parse_timestamp(item.get("publication_date")),
            )
        )
    return jobs


def parse_workingnomads(payload: list) -> list[Job]:
    jobs: list[Job] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        company = item.get("company_name") or ""
        jobs.append(
            Job(
                source="workingnomads",
                org=_slug(company),
                external_id=str(item.get("url") or item.get("title") or ""),
                title=item.get("title") or "",
                company=company,
                url=item.get("url") or "",
                apply_url=item.get("url") or "",
                locations=split_locations(item.get("location")) or ["Remote"],
                remote_flag=True,
                department=item.get("category_name"),
                description=strip_html(item.get("description")),
                posted_at=parse_timestamp(item.get("pub_date")),
            )
        )
    return jobs


def parse_jobicy(payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for item in (payload or {}).get("jobs") or []:
        company = item.get("companyName") or ""
        jobs.append(
            Job(
                source="jobicy",
                org=_slug(company),
                external_id=str(item.get("id") or ""),
                title=item.get("jobTitle") or "",
                company=company,
                url=item.get("url") or "",
                apply_url=item.get("url") or "",
                locations=["Remote"],
                location_restrictions=split_locations(item.get("jobGeo")),
                remote_flag=True,
                compensation=_salary(
                    item.get("salaryMin"), item.get("salaryMax"), item.get("salaryCurrency")
                ),
                department=(
                    item.get("jobIndustry")
                    if isinstance(item.get("jobIndustry"), str)
                    else None
                ),
                description=strip_html(item.get("jobExcerpt") or item.get("jobDescription")),
                posted_at=parse_timestamp(item.get("pubDate")),
            )
        )
    return jobs


def fetch_himalayas(client: PoliteClient, config: Config) -> list[Job]:
    """Poll Himalayas, following its cursor.

    The endpoint silently caps a page at 20 rows however large ``limit`` is, so
    reaching useful volume means paging. Worth the extra calls here and nowhere
    else: Himalayas is the only source with a ``locationRestrictions`` field,
    which is the strongest India-eligibility signal in the tool.
    """
    settings = config.source("himalayas")
    pages = max(1, int(settings.get("pages", 1)))
    jobs: list[Job] = []
    cursor: str | None = None

    for _ in range(pages):
        url = HIMALAYAS_URL.format(limit=HIMALAYAS_PAGE_SIZE)
        if cursor:
            url = f"{url}&cursor={quote(str(cursor))}"
        payload = client.get_json(url) or {}
        page_jobs = parse_himalayas(payload)
        if not page_jobs:
            break
        jobs.extend(page_jobs)
        cursor = payload.get("nextCursor")
        if not cursor:
            break
    return jobs


def fetch_remoteok(client: PoliteClient, config: Config) -> list[Job]:
    payload = client.get_json(REMOTEOK_URL)
    return parse_remoteok(payload if isinstance(payload, list) else [])


def fetch_remotive(client: PoliteClient, config: Config) -> list[Job]:
    limit = config.source("remotive").get("limit", 100)
    return parse_remotive(client.get_json(REMOTIVE_URL.format(limit=limit)) or {})


def fetch_workingnomads(client: PoliteClient, config: Config) -> list[Job]:
    payload = client.get_json(WORKINGNOMADS_URL)
    return parse_workingnomads(payload if isinstance(payload, list) else [])


def fetch_jobicy(client: PoliteClient, config: Config) -> list[Job]:
    count = config.source("jobicy").get("count", 50)
    return parse_jobicy(client.get_json(JOBICY_URL.format(count=count)) or {})
