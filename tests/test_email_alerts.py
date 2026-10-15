"""Parsing of job-alert email.

The fetcher's IMAP half is a thin shell around :func:`parse_message`; all the
logic worth testing is the parsing, so these tests never open a connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.fetchers.email_alerts import (
    EmailConfigError,
    _indeed_job_id,
    _split_company_location,
    fetch,
    parse_message,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_indeed_alert_yields_one_job_per_posting() -> None:
    jobs = parse_message(_load("indeed_alert.eml"))
    assert [j.org for j in jobs] == ["indeed", "indeed"]
    assert [j.title for j in jobs] == ["Senior Backend Engineer", "Staff Platform Engineer"]


def test_duplicate_links_in_one_email_are_collapsed() -> None:
    """Indeed repeats a posting across layout blocks; the id must dedupe it."""
    jobs = parse_message(_load("indeed_alert.eml"))
    assert len({j.external_id for j in jobs}) == len(jobs)


def test_indeed_url_is_canonicalised_not_the_tracking_link() -> None:
    job = parse_message(_load("indeed_alert.eml"))[0]
    assert job.url == "https://www.indeed.com/viewjob?jk=a1b2c3d4e5f60001"
    assert "rc/clk" in job.apply_url, "the original tracked link is still kept for applying"


def test_company_and_location_are_recovered() -> None:
    job = parse_message(_load("indeed_alert.eml"))[0]
    assert job.company == "Arcturus Labs"
    assert any("india" in loc.lower() for loc in job.locations)


def test_linkedin_alert_parses_and_canonicalises() -> None:
    jobs = parse_message(_load("linkedin_alert.eml"))
    assert len(jobs) == 2
    assert jobs[0].org == "linkedin"
    assert jobs[0].external_id == "4123456789"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4123456789/"
    assert jobs[0].title == "Principal Infrastructure Engineer"


def test_posted_at_comes_from_the_message_date() -> None:
    job = parse_message(_load("linkedin_alert.eml"))[0]
    assert job.posted_at is not None
    assert job.posted_at.year == 2026 and job.posted_at.month == 10


def test_job_keys_are_stable_and_distinct_across_providers() -> None:
    keys = {j.key for j in parse_message(_load("indeed_alert.eml"))}
    keys |= {j.key for j in parse_message(_load("linkedin_alert.eml"))}
    assert len(keys) == 4
    assert all(k.startswith("email:") for k in keys)


def test_untitled_links_are_dropped() -> None:
    """A link we cannot read a title from is noise, not a posting."""
    raw = b"""From: a@b.c\nSubject: x\nContent-Type: text/html\n\n
    <a href="https://www.indeed.com/rc/clk?jk=deadbeefdeadbeef"></a>"""
    assert parse_message(raw) == []


def test_missing_geography_is_left_empty_rather_than_guessed() -> None:
    company, locations = _split_company_location("Globex   Full-time   Posted 3 days ago")
    assert company == "Globex"
    assert locations == []


def test_indeed_job_id_extraction() -> None:
    assert _indeed_job_id("https://x.indeed.com/viewjob?jk=abc123&y=1") == "abc123"
    assert _indeed_job_id("https://x.indeed.com/viewjob?nope=1") == ""


def test_fetch_without_credentials_raises_a_clear_error(monkeypatch, config) -> None:
    for var in ("SCOUT_IMAP_HOST", "SCOUT_IMAP_USER", "SCOUT_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(EmailConfigError, match="SCOUT_IMAP_HOST"):
        fetch(None, config)
