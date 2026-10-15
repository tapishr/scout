"""Classification: is this role real, senior, engineering, and open to India?

This module is the heart of the tool and is deliberately pure — functions over
:class:`~scout.models.Job`, no I/O — so the judgement can be tested exhaustively
against fixtures without touching the network.

Two rules govern every change here:

1. **Never auto-reject on a keyword.** A posting that cannot be classified
   confidently goes to :attr:`~scout.models.Bucket.SUSPICIOUS` and is still
   printed. Silently dropping an ambiguous posting re-introduces the exact
   failure the tool exists to remove.
2. **Keep the three buckets distinct.** ``remote-india-only`` versus
   ``remote-india-or-office`` is the product, not a nicety: the second leaves the
   employer a lever to pull you onsite, which changes how you read an offer.

Geography is judged from structured location fields and explicit restriction
fields only — never from description prose. Descriptions name many countries for
reasons unrelated to eligibility ("our Berlin office", "serving EMEA customers"),
and trusting them produced more false confidence than it resolved. Description
text is used for one thing: detecting talent pools.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .config import Config
from .models import Bucket, Classification, Job, ScoutedJob

#: Review priority within the suspicious bucket (lower is read first).
#: An unqualified "Remote" might genuinely be open to India, so it earns a human's
#: attention ahead of a posting explicitly restricted to another continent.
PRIORITY_UNQUALIFIED_REMOTE = 10
PRIORITY_INDIA_UNCLEAR = 20
PRIORITY_FOREIGN_RESTRICTED = 30
PRIORITY_DEFAULT = 50


@lru_cache(maxsize=4096)
def _pattern(term: str) -> re.Pattern[str]:
    """Compile a whole-word matcher for ``term``.

    Word boundaries matter more than they look: a substring match for ``india``
    also hits "Indiana", and ``uk`` hits "Ukraine". Both produced real
    misclassifications. ``\\b`` is not used directly because terms such as
    ``u.s.`` end in punctuation, where ``\\b`` behaves counter-intuitively.
    """
    if term.endswith("*"):
        stem = re.escape(term[:-1])
        return re.compile(rf"(?<![a-z0-9]){stem}[a-z]*", re.IGNORECASE)
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE)


def _matches(haystack: str, terms: list[str]) -> list[str]:
    """Return every term in ``terms`` occurring as a whole word in ``haystack``."""
    if not haystack:
        return []
    return [term for term in terms if _pattern(term).search(haystack)]


def matches_seniority(title: str, config: Config) -> bool:
    """Whether the title signals senior/staff/principal/lead level."""
    return bool(_matches(title.lower(), config.matching.seniority_keywords))


def matches_role(title: str, config: Config) -> bool:
    """Whether the title signals engineering work."""
    return bool(_matches(title.lower(), config.matching.role_keywords))


def is_excluded_title(title: str, config: Config) -> bool:
    """Whether the title is a non-engineering role that merely reads like one.

    "Senior Sales Engineer" and "Principal Recruiter" match seniority and would
    otherwise slip through the role keywords.
    """
    return bool(_matches(title.lower(), config.matching.title_exclusions))


def is_talent_pool(job: Job, config: Config) -> bool:
    """Whether this looks like a talent pool rather than a live requisition.

    Workable in particular carries postings that exist "for active pooling
    purposes only" and explicitly do not guarantee employment. They are flagged,
    never dropped — a pool at a company worth joining is still worth knowing
    about, it just must not be mistaken for an open req.
    """
    haystack = " ".join(
        [job.title or "", job.department or "", (job.description or "")[:1500]]
    ).lower()
    return bool(_matches(haystack, config.talent_pool_markers))


def _classify_geography(job: Job, config: Config) -> tuple[Bucket, list[str], int]:
    """Decide the India-eligibility bucket from location fields.

    Returns the bucket, human-readable reasons, and a review priority used to
    order the suspicious section.
    """
    geo = config.geography
    # Only `locations` describe where the work happens. `location_restrictions`
    # state who may hold the role, which is a different claim and is handled
    # separately below — treating a restriction of "India" as a workplace made
    # a fully-remote role look like it had an Indian office attached.
    segments = [seg for seg in job.locations if seg]
    restrictions = [r for r in job.location_restrictions if r]
    blob = " | ".join([*segments, *restrictions]).lower()

    india_remote = False
    india_office = False
    unqualified_remote = False

    for segment in segments:
        text = segment.lower()
        has_india = bool(_matches(text, geo.india_terms))
        has_office = bool(_matches(text, geo.india_office_cities))
        has_remote = bool(_matches(text, geo.remote_terms))

        if has_india and has_remote:
            # "Remote - India - Remote", "Remote, India"
            india_remote = True
            if has_office:
                # "Remote - India - Bengaluru": remote, but an office is named.
                india_office = True
        elif has_india and has_office:
            # "Bengaluru - India - Bengaluru, 560071 India" -> an office, not remote.
            india_office = True
        elif has_india:
            # A bare country listing such as Ashby's location "India". Only a
            # source-level remote flag can make this remote.
            if job.remote_flag:
                india_remote = True
            else:
                india_office = True
        elif has_office:
            india_office = True
        elif has_remote:
            # "Remote - Remote", "Remote" -> remote, but no country stated.
            unqualified_remote = True

    # An explicit restriction list naming India is the strongest signal there is
    # (Himalayas locationRestrictions, Remotive candidate_required_location).
    # It is authoritative: it speaks to eligibility directly, so it can promote a
    # role to India-remote but never implies an office.
    if restrictions and _matches(" | ".join(restrictions).lower(), geo.india_terms):
        india_remote = True

    india_mentioned = bool(_matches(blob, geo.india_terms))
    foreign_regions = _matches(blob, geo.excluding_regions)

    if india_remote and not india_office:
        offices = _matches(blob, geo.deprioritised_cities)
        reasons = ["India remote listed with no Indian office attached"]
        if offices:
            reasons.append(f"mentions {', '.join(offices)}")
        return Bucket.REMOTE_INDIA_ONLY, reasons, PRIORITY_DEFAULT

    if india_remote and india_office:
        offices = _matches(blob, geo.india_office_cities)
        return (
            Bucket.REMOTE_INDIA_OR_OFFICE,
            [
                "India remote listed alongside an Indian office"
                + (f" ({', '.join(offices)})" if offices else ""),
                "employer retains a lever to pull the role onsite",
            ],
            PRIORITY_DEFAULT,
        )

    if india_mentioned or india_office:
        offices = _matches(blob, geo.india_office_cities)
        return (
            Bucket.SUSPICIOUS,
            [
                "India appears in the location"
                + (f" ({', '.join(offices)})" if offices else "")
                + " but remote eligibility is not stated"
            ],
            PRIORITY_INDIA_UNCLEAR,
        )

    # An explicit restriction list that does not name India is a statement about
    # eligibility, not an omission. Treating it as merely "unqualified remote"
    # put roles openly restricted to Argentina or Switzerland at the very top of
    # the review list, which is where the genuinely ambiguous ones belong.
    # This also means the config need not enumerate every country on earth.
    if restrictions:
        named = ", ".join(sorted({r.strip() for r in restrictions})[:4])
        return (
            Bucket.SUSPICIOUS,
            [f"explicitly open to {named} — India is not listed"],
            PRIORITY_FOREIGN_RESTRICTED,
        )

    if unqualified_remote and not foreign_regions:
        return (
            Bucket.SUSPICIOUS,
            ['says "Remote" with no country stated — verify India eligibility'],
            PRIORITY_UNQUALIFIED_REMOTE,
        )

    if unqualified_remote or (job.remote_flag and foreign_regions):
        return (
            Bucket.SUSPICIOUS,
            [f"remote but appears restricted to {', '.join(sorted(set(foreign_regions))[:4])}"],
            PRIORITY_FOREIGN_RESTRICTED,
        )

    if job.remote_flag and not segments:
        return (
            Bucket.SUSPICIOUS,
            ['flagged remote by the source but no location given'],
            PRIORITY_UNQUALIFIED_REMOTE,
        )

    return Bucket.EXCLUDED, ["no India or remote signal in the location"], PRIORITY_DEFAULT


def classify(job: Job, config: Config) -> Classification:
    """Classify one job.

    Title screening runs first and short-circuits: an excluded title is not worth
    geographic analysis. Geography decides everything else.
    """
    title = job.title or ""
    seniority = matches_seniority(title, config)
    role = matches_role(title, config)
    pool = is_talent_pool(job, config)

    if is_excluded_title(title, config):
        hits = _matches(title.lower(), config.matching.title_exclusions)
        return Classification(
            bucket=Bucket.EXCLUDED,
            seniority_match=seniority,
            role_match=role,
            is_talent_pool=pool,
            reasons=[f"title matches exclusion: {', '.join(hits)}"],
        )

    if not role or not seniority:
        missing = []
        if not seniority:
            missing.append("seniority")
        if not role:
            missing.append("engineering role")
        return Classification(
            bucket=Bucket.EXCLUDED,
            seniority_match=seniority,
            role_match=role,
            is_talent_pool=pool,
            reasons=[f"title does not signal {' or '.join(missing)}"],
        )

    bucket, reasons, priority = _classify_geography(job, config)
    if pool:
        reasons = [*reasons, "looks like a talent pool, not a live requisition"]
    return Classification(
        bucket=bucket,
        seniority_match=seniority,
        role_match=role,
        is_talent_pool=pool,
        reasons=reasons,
        review_priority=priority,
    )


def classify_all(jobs: list[Job], config: Config) -> list[ScoutedJob]:
    """Classify a batch, preserving input order."""
    return [ScoutedJob(job=job, classification=classify(job, config)) for job in jobs]


def review_priority(scouted: ScoutedJob) -> int:
    """Read-order within a bucket; ambiguous postings outrank clearly foreign ones."""
    return scouted.classification.review_priority


def is_deprioritised(job: Job, config: Config) -> bool:
    """Whether the role is tied to a city the operator ranks last (Bengaluru).

    Used only for ordering inside a bucket. Never a reason to drop a posting.
    """
    return bool(_matches(job.location_blob, config.geography.deprioritised_cities))
