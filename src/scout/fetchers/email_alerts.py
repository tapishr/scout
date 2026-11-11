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

Credentials never live in config, because config is committed and this repo is
public. They are read from the process environment, or from a gitignored
``./.env``, or from ``~/.config/scout/env`` — see :mod:`scout.secrets`. A value
of ``keychain:<service>`` is resolved through the macOS keychain instead:

    SCOUT_IMAP_HOST      e.g. imap.gmail.com
    SCOUT_IMAP_USER      the mailbox address
    SCOUT_IMAP_PASSWORD  an app-specific password, not the account password
    SCOUT_IMAP_PORT      optional, defaults to 993 (always SSL)
"""

from __future__ import annotations

import base64
import email
import gzip
import imaplib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from email.message import Message
from html import unescape
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from ..secrets import get as secret

#: Where a job id lives in each provider's alert links.
INDEED_LINK = re.compile(
    r"https?://[\w.-]*indeed\.[\w.]+/(?:rc/clk|viewjob|pagead/clk)[^\s\"'<>]*", re.I
)
#: Indeed wraps every job link in a click tracker whose path segment is
#: url-safe base64 of gzipped JSON holding the real URL. The job id is only
#: reachable by decoding it — there is no jk= parameter on the outer link.
INDEED_TRACKER = re.compile(r"https?://cts\.indeed\.com/v3/([A-Za-z0-9_\-]+)", re.I)
#: LinkedIn alert links carry a /comm prefix; direct /jobs/view also occurs.
LINKEDIN_LINK = re.compile(
    r"https?://[\w.-]*linkedin\.com/(?:comm/)?jobs/view/(\d+)[^\s\"'<>]*", re.I
)

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


def _decode_indeed_tracker(token: str) -> str:
    """Recover the destination URL from a cts.indeed.com path segment.

    The segment is url-safe base64 (padding stripped) of gzipped JSON shaped
    ``{"u": "<real url>", ...}``. Anything that fails to decode is not a job
    link — unsubscribe and not-interested links share the same wrapper — so the
    caller simply skips it.
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = gzip.decompress(base64.urlsafe_b64decode(padded)).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — a non-decodable token is just not a job link
        return ""
    try:
        return str(json.loads(raw).get("u") or "")
    except (ValueError, AttributeError):
        match = re.search(r'"u"\s*:\s*"([^"]+)"', raw)
        return match.group(1) if match else ""


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


ANCHOR = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
#: Trailing call-to-action words that are button labels, not part of a title.
_CTA = re.compile(r"\s*(view job|view|apply now|apply|see job)\s*$", re.I)
#: "Senior AI Engineer @ JoVE" — Indeed puts title and company in the subject.
SUBJECT_SPLIT = re.compile(r"^(?P<title>.+?)\s+(?:@|at)\s+(?P<company>.+)$", re.I)


def _anchor_titles(body: str) -> dict[str, str]:
    """Map each job URL to the best title text linking to it.

    Alert emails link the same posting several times — a logo with no text, the
    title, and a "View job" button. Taking the longest anchor text per target
    picks the title and discards the rest, without modelling either provider's
    table layout (which is deeply nested and changes often).
    """
    best: dict[str, str] = {}
    for match in ANCHOR.finditer(body):
        href, inner = match.group(1), _text(match.group(2))
        inner = _CTA.sub("", inner).strip()
        if not inner:
            continue
        if len(inner) > len(best.get(href, "")):
            best[href] = inner
    return best


def _title_for(url: str, titles: dict[str, str]) -> str:
    """Longest anchor text whose href contains this url (or vice versa)."""
    if url in titles:
        return titles[url]
    candidates = [t for href, t in titles.items() if url in href or href in url]
    return max(candidates, key=len) if candidates else ""


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

    subject = _text(str(message.get("Subject") or ""))
    subject_match = SUBJECT_SPLIT.match(subject)
    subj_title = subject_match.group("title").strip() if subject_match else ""
    subj_company = subject_match.group("company").strip() if subject_match else ""

    jobs: list[Job] = []
    seen: set[str] = set()

    for body in _html_parts(message):
        titles = _anchor_titles(body)
        indeed_links: list[tuple[str, str]] = [
            (u, _indeed_job_id(u)) for u in INDEED_LINK.findall(body)
        ]
        for match in INDEED_TRACKER.finditer(body):
            destination = _decode_indeed_tracker(match.group(1))
            if destination:
                indeed_links.append((match.group(0), _indeed_job_id(destination)))
        for url, job_id in indeed_links:
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            # Indeed alerts are one job per mail and the subject is the title;
            # the only anchor text is a "View job" button, so prefer the subject.
            title = subj_title or _title_for(url, titles)
            company = subj_company
            locations: list[str] = []
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
            # LinkedIn anchor text is "<title><company> · <location>". The
            # title/company boundary has no separator and is not recoverable,
            # but the location is — and location is what the India filter needs.
            anchor = _title_for(url, titles)
            title, _, tail = anchor.partition("·")
            title = title.strip() or anchor
            company = ""
            locations = [tail.strip()] if tail.strip() else []
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
    host = secret("SCOUT_IMAP_HOST")
    user = secret("SCOUT_IMAP_USER")
    password = secret("SCOUT_IMAP_PASSWORD")
    if not (host and user and password):
        raise EmailConfigError(
            "missing IMAP credentials. Set SCOUT_IMAP_HOST, SCOUT_IMAP_USER and "
            "SCOUT_IMAP_PASSWORD in the environment, in ./.env, or in "
            "~/.config/scout/env (see README)"
        )

    folder = settings.get("folder") or "INBOX"
    max_age_days = int(settings.get("max_age_days", 7))
    since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%d-%b-%Y")

    jobs: list[Job] = []
    conn = imaplib.IMAP4_SSL(host, int(secret("SCOUT_IMAP_PORT", "993") or 993))
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
