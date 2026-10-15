"""Tests for digest rendering.

The digest's shape is a product decision — it is read every morning over coffee
— so these tests assert on ordering and on what is never hidden, not on exact
prose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scout.digest import render
from scout.models import Bucket, Classification, DiffResult, Job, ScoutedJob

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


def make(title, bucket, company="Atlassian", locations=None, days_old=3, **kwargs) -> ScoutedJob:
    job = Job(
        source="atlassian",
        org="atlassian",
        external_id=kwargs.pop("external_id", title),
        title=title,
        company=company,
        url="https://example.invalid/job",
        apply_url="https://example.invalid/apply",
        locations=locations or ["Remote - India - Remote"],
        posted_at=NOW - timedelta(days=days_old),
        **kwargs,
    )
    return ScoutedJob(
        job=job,
        classification=Classification(
            bucket=bucket,
            seniority_match=True,
            role_match=True,
            reasons=["because"],
            review_priority=kwargs.pop("review_priority", 50),
        ),
    )


def render_with(new=None, disappeared=None, config=None, **kwargs):
    result = DiffResult(new=new or [], disappeared=disappeared or [], carried_over=[])
    return render(
        result,
        config,
        sources_ok=kwargs.pop("sources_ok", ["atlassian"]),
        sources_failed=kwargs.pop("sources_failed", {}),
        total_matched=kwargs.pop("total_matched", len(new or [])),
        now=NOW,
        **kwargs,
    )


class TestSummary:
    def test_headline_counts_are_present(self, config):
        out = render_with(
            new=[make("Senior Infrastructure Engineer", Bucket.REMOTE_INDIA_ONLY)],
            disappeared=[make("Staff Engineer", Bucket.REMOTE_INDIA_ONLY).job],
            config=config,
        )
        assert "**1 new, 1 disappeared**" in out

    def test_quiet_run_says_so(self, config):
        assert "Nothing new since the last run." in render_with(config=config)

    def test_first_run_is_labelled(self, config):
        result = DiffResult(new=[], disappeared=[], carried_over=[], is_first_run=True)
        out = render(
            result, config, sources_ok=["atlassian"], sources_failed={}, total_matched=0, now=NOW
        )
        assert "first run" in out


class TestOrdering:
    def test_best_bucket_comes_first(self, config):
        out = render_with(
            new=[
                make("Senior A", Bucket.SUSPICIOUS, locations=["Remote"]),
                make("Senior B", Bucket.REMOTE_INDIA_OR_OFFICE),
                make("Senior C", Bucket.REMOTE_INDIA_ONLY),
            ],
            config=config,
        )
        only = out.index("no office attached")
        or_office = out.index("but an office is listed")
        human = out.index("Needs a human look")
        assert only < or_office < human

    def test_bengaluru_roles_sort_last_within_a_bucket(self, config):
        out = render_with(
            new=[
                make(
                    "Senior Bengaluru Engineer",
                    Bucket.REMOTE_INDIA_ONLY,
                    locations=["Remote - India - Remote", "Bengaluru, India"],
                    days_old=1,
                ),
                make("Senior Elsewhere Engineer", Bucket.REMOTE_INDIA_ONLY, days_old=9),
            ],
            config=config,
        )
        assert out.index("Senior Elsewhere Engineer") < out.index("Senior Bengaluru Engineer")


class TestSuspiciousSection:
    def test_suspicious_is_never_silently_dropped(self, config):
        """The failure this tool exists to prevent."""
        out = render_with(
            new=[make("Senior Ambiguous Engineer", Bucket.SUSPICIOUS, locations=["Remote"])],
            config=config,
        )
        assert "Senior Ambiguous Engineer" in out
        assert "Needs a human look" in out

    def test_long_suspicious_list_is_capped_but_the_remainder_is_announced(self, config):
        cap = int(config.output.get("max_suspicious", 40))
        jobs = [
            make(
                f"Senior Engineer {i}",
                Bucket.SUSPICIOUS,
                locations=["Remote"],
                external_id=str(i),
            )
            for i in range(cap + 5)
        ]
        out = render_with(new=jobs, config=config)
        assert "and 5 more" in out
        assert f"Needs a human look ({cap + 5})" in out

    def test_excluded_is_hidden_by_default_and_revealed_on_request(self, config):
        jobs = [make("Senior Nope Engineer", Bucket.EXCLUDED, locations=["Paris"])]
        assert "Senior Nope Engineer" not in render_with(new=jobs, config=config)
        assert "Senior Nope Engineer" in render_with(new=jobs, config=config, show_excluded=True)


class TestJobRendering:
    def test_entry_carries_the_facts_needed_to_triage(self, config):
        out = render_with(
            new=[make("Senior SRE", Bucket.REMOTE_INDIA_ONLY, compensation="₹80L–1.2Cr")],
            config=config,
        )
        assert "Senior SRE" in out
        assert "Atlassian" in out
        assert "₹80L–1.2Cr" in out
        assert "posted 3d ago" in out
        assert "https://example.invalid/apply" in out

    def test_talent_pool_is_labelled(self, config):
        scouted = make("Senior Pool Engineer", Bucket.REMOTE_INDIA_ONLY)
        scouted.classification.is_talent_pool = True
        assert "TALENT POOL" in render_with(new=[scouted], config=config)

    def test_missing_date_does_not_break_rendering(self, config):
        scouted = make("Senior Undated Engineer", Bucket.REMOTE_INDIA_ONLY)
        scouted.job.posted_at = None
        assert "age unknown" in render_with(new=[scouted], config=config)


class TestFooter:
    def test_failed_sources_are_reported_not_hidden(self, config):
        out = render_with(config=config, sources_failed={"remoteok": "Timeout"})
        assert "Failed sources" in out and "remoteok" in out and "Timeout" in out

    def test_dead_slugs_are_surfaced(self, config):
        """A 404 board drops a whole company silently; the digest must say so."""
        out = render_with(
            config=config,
            dead_urls=["https://boards-api.greenhouse.io/v1/boards/zapier/jobs"],
        )
        assert "404" in out and "zapier" in out

    def test_suppressed_disappearance_detection_is_explained(self, config):
        result = DiffResult(new=[], disappeared=[], carried_over=[], skipped_sources=["remoteok"])
        out = render(
            result,
            config,
            sources_ok=["atlassian"],
            sources_failed={"remoteok": "boom"},
            total_matched=0,
            now=NOW,
        )
        assert "Disappearance detection was skipped" in out
