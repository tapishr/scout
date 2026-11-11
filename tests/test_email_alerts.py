"""Parsing of job-alert email.

Fixtures mirror the link shapes the real providers actually send, which are not
the obvious ones: Indeed wraps every job in a ``cts.indeed.com`` tracker whose
path is url-safe base64 of gzipped JSON, and LinkedIn alert links carry a
``/comm`` prefix. Both were discovered by reading real mail; parsing was written
against assumptions first and found nothing.

The IMAP half is a thin shell around :func:`parse_message`, so these tests never
open a connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.fetchers.email_alerts import (
    EmailConfigError,
    _decode_indeed_tracker,
    _indeed_job_id,
    fetch,
    parse_message,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestIndeed:
    def test_job_is_recovered_from_the_click_tracker(self) -> None:
        """The id exists only inside the gzipped tracker payload."""
        jobs = parse_message(_load("indeed_alert.eml"))
        assert [j.external_id for j in jobs] == ["a1b2c3d4e5f60001"]

    def test_title_and_company_come_from_the_subject(self) -> None:
        """Indeed's only anchor text is a 'View job' button, so the subject wins."""
        job = parse_message(_load("indeed_alert.eml"))[0]
        assert job.title == "Senior Backend Engineer"
        assert job.company == "Arcturus Labs"

    def test_repeated_links_collapse_to_one_job(self) -> None:
        assert len(parse_message(_load("indeed_alert.eml"))) == 1

    def test_undecodable_tracker_is_skipped_not_fatal(self) -> None:
        """Unsubscribe links share the tracker shape and must not break a run."""
        assert _decode_indeed_tracker("not-a-valid-gzip-token") == ""
        assert len(parse_message(_load("indeed_alert.eml"))) == 1

    def test_url_is_canonicalised(self) -> None:
        job = parse_message(_load("indeed_alert.eml"))[0]
        assert job.url == "https://www.indeed.com/viewjob?jk=a1b2c3d4e5f60001"
        assert "cts.indeed.com" in job.apply_url, "the tracked link is kept for applying"


class TestLinkedIn:
    def test_comm_prefixed_links_are_matched(self) -> None:
        jobs = parse_message(_load("linkedin_alert.eml"))
        assert {j.external_id for j in jobs} == {"4123456789", "4987654321"}

    def test_title_is_the_longest_anchor_not_the_button(self) -> None:
        """Each job is linked by a logo, the title, and a 'View job' button."""
        job = next(j for j in parse_message(_load("linkedin_alert.eml"))
                   if j.external_id == "4123456789")
        assert job.title.startswith("Principal Infrastructure Engineer")
        assert "View job" not in job.title

    def test_location_is_split_off_the_middot(self) -> None:
        job = next(j for j in parse_message(_load("linkedin_alert.eml"))
                   if j.external_id == "4123456789")
        assert job.locations == ["India (Remote)"]

    def test_location_feeds_the_india_filter(self) -> None:
        """The whole point of recovering location from the anchor text."""
        job = next(j for j in parse_message(_load("linkedin_alert.eml"))
                   if j.external_id == "4987654321")
        assert "pune" in job.location_blob


class TestGeneral:
    def test_posted_at_comes_from_the_message_date(self) -> None:
        job = parse_message(_load("linkedin_alert.eml"))[0]
        assert job.posted_at is not None and job.posted_at.year == 2026

    def test_malformed_date_costs_the_age_not_the_posting(self) -> None:
        raw = (b"From: a@b.c\nSubject: Staff Engineer @ Globex\n"
               b"Content-Type: text/html\n\n"
               b'<a href="https://www.linkedin.com/comm/jobs/view/999/">Staff Engineer</a>')
        jobs = parse_message(raw)
        assert len(jobs) == 1 and jobs[0].posted_at is None

    def test_untitled_links_are_dropped(self) -> None:
        raw = (b"From: a@b.c\nSubject: x\nContent-Type: text/html\n\n"
               b'<a href="https://www.linkedin.com/comm/jobs/view/999/"></a>')
        assert parse_message(raw) == []

    def test_keys_are_distinct_across_providers(self) -> None:
        keys = {j.key for j in parse_message(_load("indeed_alert.eml"))}
        keys |= {j.key for j in parse_message(_load("linkedin_alert.eml"))}
        assert len(keys) == 3 and all(k.startswith("email:") for k in keys)

    def test_indeed_job_id_extraction(self) -> None:
        assert _indeed_job_id("https://x.indeed.com/viewjob?jk=abc123&y=1") == "abc123"
        assert _indeed_job_id("https://x.indeed.com/viewjob?nope=1") == ""

    def test_fetch_without_credentials_raises_a_clear_error(self, monkeypatch, config) -> None:
        for var in ("SCOUT_IMAP_HOST", "SCOUT_IMAP_USER", "SCOUT_IMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr("scout.fetchers.email_alerts.secret", lambda n, d=None: d)
        with pytest.raises(EmailConfigError, match="SCOUT_IMAP_HOST"):
            fetch(None, config)
