"""Markdown digest rendering.

This is read every morning over coffee, so the shape of the output is a product
decision, not a formatting detail:

* Best news first — ``remote-india-only`` leads, always.
* Every entry states *why* it was classified that way, so the reader can
  disagree with the classifier without re-reading the posting.
* ``suspicious`` is printed, never hidden, but is capped and ordered so the
  genuinely ambiguous postings come before the obviously foreign ones.
* Disappeared roles get their own section: a pulled requisition is signal.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .filters import is_deprioritised
from .models import BUCKET_ORDER, Bucket, DiffResult, Job, ScoutedJob

BUCKET_HEADINGS = {
    Bucket.REMOTE_INDIA_ONLY: "Remote, India — no office attached",
    Bucket.REMOTE_INDIA_OR_OFFICE: "Remote India, but an office is listed",
    Bucket.SUSPICIOUS: "Needs a human look",
    Bucket.EXCLUDED: "Excluded (shown for filter tuning)",
}

BUCKET_BLURBS = {
    Bucket.REMOTE_INDIA_ONLY: "India remote with no Indian office on the requisition.",
    Bucket.REMOTE_INDIA_OR_OFFICE: (
        "Viable, but the employer has a lever to pull the role onsite."
    ),
    Bucket.SUSPICIOUS: (
        "Not auto-rejected — these look open but may not be. Ambiguous ones first."
    ),
    Bucket.EXCLUDED: "Did not match the seniority or role filters.",
}


def _age(job: Job, now: datetime) -> str:
    days = job.age_days(now)
    if days is None:
        return "age unknown"
    if days == 0:
        return "posted today"
    if days == 1:
        return "posted yesterday"
    return f"posted {days}d ago"


def _sort_key(scouted: ScoutedJob, config: Config, now: datetime) -> tuple:
    """Ambiguous first, Bengaluru-tied last, then newest."""
    days = scouted.job.age_days(now)
    return (
        scouted.classification.review_priority,
        1 if is_deprioritised(scouted.job, config) else 0,
        days if days is not None else 9999,
        scouted.job.company.lower(),
    )


def _render_job(scouted: ScoutedJob, now: datetime) -> list[str]:
    job = scouted.job
    classification = scouted.classification
    title = job.title or "(untitled)"
    company = job.company or job.org
    flag = " `TALENT POOL`" if classification.is_talent_pool else ""

    facts = [", ".join(job.locations) or "location not stated", _age(job, now)]
    if job.compensation:
        facts.append(job.compensation)
    if job.location_restrictions:
        facts.append("open to: " + ", ".join(job.location_restrictions))

    lines = [f"- **{title}** — {company}{flag}"]
    lines.append(f"  {' · '.join(facts)}")
    if classification.reasons:
        lines.append(f"  _{'; '.join(classification.reasons)}_")
    if job.apply_url or job.url:
        lines.append(f"  <{job.apply_url or job.url}>")
    return lines


def render(
    result: DiffResult,
    config: Config,
    *,
    sources_ok: list[str],
    sources_failed: dict[str, str],
    total_matched: int,
    show_excluded: bool = False,
    dead_urls: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Render the digest as markdown."""
    now = now or datetime.now(timezone.utc)
    max_suspicious = int(config.output.get("max_suspicious", 40))

    out: list[str] = []
    out.append(f"# scout — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    out.append("")

    headline = f"**{len(result.new)} new, {len(result.disappeared)} disappeared**"
    if result.is_first_run:
        headline += "  _(first run — everything currently matching is reported as new)_"
    out.append(headline)
    out.append("")
    out.append(
        f"{total_matched} roles matched the filters across "
        f"{len(sources_ok)} source(s)."
        + (f" {len(sources_failed)} source(s) failed." if sources_failed else "")
    )
    out.append("")

    by_bucket: dict[Bucket, list[ScoutedJob]] = {bucket: [] for bucket in BUCKET_ORDER}
    for scouted in result.new:
        by_bucket[scouted.classification.bucket].append(scouted)

    for bucket in BUCKET_ORDER:
        if bucket is Bucket.EXCLUDED and not show_excluded:
            continue
        entries = sorted(by_bucket[bucket], key=lambda s: _sort_key(s, config, now))
        if not entries:
            continue

        shown = entries
        truncated = 0
        if bucket is Bucket.SUSPICIOUS and len(entries) > max_suspicious:
            shown = entries[:max_suspicious]
            truncated = len(entries) - max_suspicious

        out.append(f"## {BUCKET_HEADINGS[bucket]} ({len(entries)})")
        out.append(f"_{BUCKET_BLURBS[bucket]}_")
        out.append("")
        for scouted in shown:
            out.extend(_render_job(scouted, now))
            out.append("")
        if truncated:
            out.append(f"_… and {truncated} more; raise `output.max_suspicious` to see them._")
            out.append("")

    if result.disappeared:
        out.append(f"## Disappeared since the last run ({len(result.disappeared)})")
        out.append("_A pulled requisition is signal too — especially if you applied._")
        out.append("")
        for job in sorted(result.disappeared, key=lambda j: (j.company.lower(), j.title)):
            location = ", ".join(job.locations) or "location not stated"
            out.append(f"- **{job.title or '(untitled)'}** — {job.company or job.org} · {location}")
        out.append("")

    if not result.new and not result.disappeared:
        out.append("Nothing new since the last run.")
        out.append("")

    out.append("---")
    out.append("")
    out.append(f"Sources polled: {', '.join(sorted(sources_ok)) or 'none'}")
    if sources_failed:
        out.append("")
        out.append("**Failed sources** (the run continued without them):")
        for name, error in sorted(sources_failed.items()):
            out.append(f"- `{name}`: {error}")
    if dead_urls:
        out.append("")
        out.append(
            f"**{len(dead_urls)} board(s) returned 404** — a dead slug drops a whole "
            "company silently, so check these in `config.yaml`:"
        )
        for url in sorted(set(dead_urls))[:12]:
            out.append(f"- `{url}`")

    if result.skipped_sources:
        out.append("")
        out.append(
            "Disappearance detection was skipped for "
            f"{', '.join(result.skipped_sources)} because they failed this run."
        )
    out.append("")
    return "\n".join(out)
