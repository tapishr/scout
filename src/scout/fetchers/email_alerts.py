"""Job-alert emails, read over IMAP.

Indeed and LinkedIn both forbid crawling the pages this tool would otherwise
want. Indeed's ``robots.txt`` carries ``Disallow: /jobs/IN/`` under
``User-agent: *`` — the exact path for Indian listings — along with
``Disallow: /*?rss``, and the RSS endpoint answers 403 regardless. LinkedIn's
User Agreement §8.2 prohibits automated access outright, and an account
restriction there would cost more than the listings are worth.

So this fetcher does not touch either site. It reads **alert emails the user
asked those platforms to send**, out of the user's own mailbox. That is
first-party data, delivered on request, parsed locally. No scraping, no bot
detection, no account risk, and nothing to repair when a page template changes.

Setting it up: create saved searches on Indeed and LinkedIn, have the alerts
filtered into one mail folder, and point ``folder`` at it.

Credentials come from the environment and never from config, because config is
committed and this repo is public:

    SCOUT_IMAP_HOST      e.g. imap.gmail.com
    SCOUT_IMAP_USER      the mailbox address
    SCOUT_IMAP_PASSWORD  an app-specific password, not the account password
    SCOUT_IMAP_PORT      optional, defaults to 993 (always SSL)
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from email.message import Message
from html import unescape
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..http import PoliteClient
from ..models import Job

#: Where a job id lives in each provider's alert links.
INDEED_LINK = re.compile(
    r"https?://[\w.-]*indeed\.[\w.]+/(?:rc/clk|viewjob|pagead/clk)[^\s\"'<>]*", re.I
)
LINKEDIN_LINK = re.compile(r"https?://[\w.-]*linkedin\.com/jobs/view/(\d+)[^\s\"'<>]*", re.I)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
#: Block-level ends become an explicit separator before tags are stripped —
#: otherwise whitespace collapsing erases the only boundary between the
#: company and the location, which alerts render as adjacent blocks.
_BLOCK_END = re.compile(r"</(?:div|td|tr|p|span|li|h[1-6])>|<br\s*/?>", re.I)
_SEP = "\x1f"
#: Words that mark a chunk as geography rather than a company or a salary.
_GEO_HINT = re.compile(
    r"remote|hybrid|onsite|india|bengaluru|bangalore|mumbai|pune|hyderabad"
    r"|delhi|chennai|noida|gurgaon|gurugram|kolkata|ahmedabad",
    re.I,
)


class EmailConfigError(RuntimeError):
    """Raised when IMAP credentials are absent. Surfaced as a source error."""


def _text(html: str) -> str:
    """Flatten a fragment of email HTML to readable text."""
    return _WS.sub(" ", unescape(_TAG.sub(" ", html))).strip()


def _blocks(html: str) -> list[str]:
    """Split a fragment into its block-level pieces, in order.

    Preserves the structure that whitespace collapsing would destroy, so the
    company and the location stay separate items rather than one run-on string.
    """
    marked = _BLOCK_END.sub(_SEP, html)
    return [t for t in (_text(chunk) for chunk in marked.split(_SEP)) if t]


def _indeed_job_id(url: str) -> str:
    """Indeed carries the posting id in the ``jk`` query parameter."""
    return (parse_qs(urlparse(url).query).get("jk") or [""])[0]


def _html_parts(message: Message) -> Iterable[str]:
    """Yield decoded ``text/html`` bodies, falling back to ``text/plain``."""
    for wanted in ("text/html", "text/plain"):
        found = False
        for part in message.walk():
            if part.get_content_type() != wanted:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            yield payload.decode(charset, errors="replace")
            found = True
        if found:
            return


def _anchor_context(html: str, link: str) -> tuple[str, str]:
    """Return (anchor text, following text) around a link.

    Both providers put the job title inside the anchor and the company and
    location in the markup immediately after it. This reads that window rather
    than modelling either template, so a layout change degrades the extras
    instead of losing the posting.
    """
    idx = html.find(link)
    if idx < 0:
        return "", ""
    open_tag = html.rfind("<a", max(0, idx - 400), idx)
    close = html.find("</a>", idx)
    anchor = _text(html[html.find(">", idx) + 1 : close]) if close > 0 else ""
    if open_tag >= 0 and close > 0:
        anchor = _text(html[html.find(">", open_tag) + 1 : close])
    trailing = _SEP.join(_blocks(html[close + 4 : close + 600])) if close > 0 else ""
    return anchor, trailing


def _split_company_location(trailing: str) -> tuple[str, list[str]]:
    """Best-effort company and location from the text following an anchor.

    Alerts render these as separate blocks, which flatten to a run of short
    phrases. The first is the company; a later one naming a place is the
    location. Anything unrecognised is left empty rather than guessed at —
    the classifier treats absent geography as ``SUSPICIOUS``, which is the
    correct outcome for a posting we could not read properly.
    """
    raw = trailing.split(_SEP) if _SEP in trailing else re.split(r"\s{2,}|\s[·|]\s", trailing)
    chunks = [c.strip(" -·|,") for c in raw if c.strip(" -·|,")]
    chunks = [c for c in chunks if 1 < len(c) < 80][:6]
    if not chunks:
        return "", []
    company = chunks[0]
    locations = [c for c in chunks[1:] if _GEO_HINT.search(c)]
    return company, locations


def parse_message(raw: bytes, received: datetime | None = None) -> list[Job]:
    """Extract postings from one alert email. Pure — no network, no IMAP."""
    message = email.message_from_bytes(raw)
    stamp = received
    if stamp is None:
        try:
            stamp = email.utils.parsedate_to_datetime(message.get("Date") or "")
        except (TypeError, ValueError):
            # A missing or malformed Date costs us the age, not the posting.
            stamp = None
    if stamp is not None and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    jobs: list[Job] = []
    seen: set[str] = set()

    for body in _html_parts(message):
        for url in INDEED_LINK.findall(body):
            job_id = _indeed_job_id(url)
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            title, trailing = _anchor_context(body, url)
            company, locations = _split_company_location(trailing)
            jobs.append(
                Job(
                    source="email",
                    org="indeed",
                    external_id=job_id,
                    title=title,
                    company=company,
                    url=f"https://www.indeed.com/viewjob?jk={job_id}",
                    apply_url=url,
                    locations=locations,
                    posted_at=stamp,
                )
            )
        for match in LINKEDIN_LINK.finditer(body):
            url, job_id = match.group(0), match.group(1)
            if job_id in seen:
                continue
            seen.add(job_id)
            title, trailing = _anchor_context(body, url)
            company, locations = _split_company_location(trailing)
            jobs.append(
                Job(
                    source="email",
                    org="linkedin",
                    external_id=job_id,
                    title=title,
                    company=company,
                    url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                    apply_url=url,
                    locations=locations,
                    posted_at=stamp,
                )
            )
    return [j for j in jobs if j.title]


def fetch(client: PoliteClient, config: Config) -> list[Job]:  # noqa: ARG001 — IMAP, not HTTP
    """Read recent alert emails from the configured folder and normalise them."""
    settings = config.source("email")
    host = os.environ.get("SCOUT_IMAP_HOST")
    user = os.environ.get("SCOUT_IMAP_USER")
    password = os.environ.get("SCOUT_IMAP_PASSWORD")
    if not (host and user and password):
        raise EmailConfigError(
            "set SCOUT_IMAP_HOST, SCOUT_IMAP_USER and SCOUT_IMAP_PASSWORD to read job-alert email"
        )

    folder = settings.get("folder") or "INBOX"
    max_age_days = int(settings.get("max_age_days", 7))
    since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%d-%b-%Y")

    jobs: list[Job] = []
    conn = imaplib.IMAP4_SSL(host, int(os.environ.get("SCOUT_IMAP_PORT", 993)))
    try:
        conn.login(user, password)
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            return []
        for num in (data[0] or b"").split():
            status, payload = conn.fetch(num, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            jobs.extend(parse_message(payload[0][1]))
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 — logout failure must not lose a run's jobs
            pass
    return jobs
