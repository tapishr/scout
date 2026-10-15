"""Tests for classification — the part of the tool with real judgement in it.

The cases below are drawn from live payloads. Atlassian requisition 25283 is the
canonical ``remote-india-only`` posting and is treated here as a regression
anchor: if it ever stops classifying that way, the tool's core claim is broken.
"""

from __future__ import annotations

import pytest

from scout.filters import (
    PRIORITY_FOREIGN_RESTRICTED,
    PRIORITY_UNQUALIFIED_REMOTE,
    classify,
    is_deprioritised,
    is_talent_pool,
    matches_role,
    matches_seniority,
)
from scout.models import Bucket, Job


def make_job(title="Senior Infrastructure Engineer", locations=None, **kwargs) -> Job:
    return Job(
        source=kwargs.pop("source", "atlassian"),
        org=kwargs.pop("org", "atlassian"),
        external_id=kwargs.pop("external_id", "1"),
        title=title,
        company=kwargs.pop("company", "Atlassian"),
        url=kwargs.pop("url", "https://example.invalid/job"),
        locations=locations if locations is not None else [],
        **kwargs,
    )


class TestBucketing:
    def test_canonical_remote_india_only(self, config):
        """Atlassian req 25283: India remote, no office. The best possible case."""
        job = make_job(locations=["Remote - India - Remote", "Remote - Remote"])
        assert classify(job, config).bucket is Bucket.REMOTE_INDIA_ONLY

    def test_india_remote_plus_office_is_a_separate_bucket(self, config):
        """An attached Bengaluru office is a lever to pull the role onsite."""
        job = make_job(
            title="Principal Data Platform Engineer",
            locations=[
                "Remote - India - Remote",
                "Remote - Remote",
                "Bengaluru - India -   Bengaluru,  560071 India",
            ],
        )
        result = classify(job, config)
        assert result.bucket is Bucket.REMOTE_INDIA_OR_OFFICE
        assert any("onsite" in reason for reason in result.reasons)

    def test_indian_office_without_remote_is_suspicious_not_excluded(self, config):
        """An onsite-India role is surfaced for a human, never silently dropped."""
        job = make_job(
            title="Principal Data Engineer",
            locations=["Bengaluru - India -   Bengaluru,  560071 India", "Remote - Remote"],
        )
        assert classify(job, config).bucket is Bucket.SUSPICIOUS

    def test_bare_remote_is_suspicious_and_read_first(self, config):
        """"Remote" with no country might be open to India, so it ranks high."""
        result = classify(make_job(locations=["Remote"]), config)
        assert result.bucket is Bucket.SUSPICIOUS
        assert result.review_priority == PRIORITY_UNQUALIFIED_REMOTE

    def test_remote_us_only_is_suspicious_but_ranked_lower(self, config):
        result = classify(make_job(locations=["Remote, United States"]), config)
        assert result.bucket is Bucket.SUSPICIOUS
        assert result.review_priority == PRIORITY_FOREIGN_RESTRICTED

    def test_bare_india_with_source_remote_flag_counts_as_india_remote(self, config):
        """Ashby gives location "India" plus isRemote; together that is remote-India."""
        job = make_job(
            title="Senior Consulting Engineer - India",
            locations=["India"],
            remote_flag=True,
            source="ashby",
            org="clickhouse",
        )
        assert classify(job, config).bucket is Bucket.REMOTE_INDIA_ONLY

    def test_bare_india_without_remote_flag_is_not_promoted(self, config):
        job = make_job(locations=["India"], remote_flag=None)
        assert classify(job, config).bucket is Bucket.SUSPICIOUS

    def test_explicit_restriction_naming_india_is_authoritative(self, config):
        """Himalayas locationRestrictions is the strongest signal available."""
        job = make_job(
            source="himalayas", locations=["Remote"], location_restrictions=["India"]
        )
        assert classify(job, config).bucket is Bucket.REMOTE_INDIA_ONLY

    def test_restriction_list_without_india_ranks_below_true_ambiguity(self, config):
        """A role "open to: Argentina" is not ambiguous — it is a no, and must
        not crowd out genuinely unclear postings at the top of the review list."""
        job = make_job(
            source="himalayas",
            locations=["Remote"],
            location_restrictions=["Argentina"],
        )
        result = classify(job, config)
        assert result.bucket is Bucket.SUSPICIOUS
        assert result.review_priority == PRIORITY_FOREIGN_RESTRICTED
        assert "India is not listed" in result.reasons[0]

    def test_multi_country_restriction_including_india_still_wins(self, config):
        """Wikimedia-style postings list 20 countries; India among them is a yes."""
        job = make_job(
            source="himalayas",
            locations=["Remote"],
            location_restrictions=["Brazil", "Canada", "India", "Kenya"],
        )
        assert classify(job, config).bucket is Bucket.REMOTE_INDIA_ONLY

    def test_onsite_foreign_role_is_excluded(self, config):
        job = make_job(locations=["San Francisco, California"])
        assert classify(job, config).bucket is Bucket.EXCLUDED


class TestTitleScreening:
    @pytest.mark.parametrize(
        "title", ["Senior Engineer", "Staff SRE", "Principal Architect", "Head of Platform"]
    )
    def test_senior_engineering_titles_pass(self, config, title):
        assert matches_seniority(title, config)
        assert matches_role(title, config)

    def test_junior_title_is_excluded(self, config):
        job = make_job(title="Software Engineer", locations=["Remote - India - Remote"])
        result = classify(job, config)
        assert result.bucket is Bucket.EXCLUDED
        assert not result.seniority_match

    @pytest.mark.parametrize(
        "title",
        ["Senior Sales Engineer", "Principal Recruiter", "Senior Solutions Engineer"],
    )
    def test_non_engineering_titles_that_look_senior_are_excluded(self, config, title):
        job = make_job(title=title, locations=["Remote - India - Remote"])
        result = classify(job, config)
        assert result.bucket is Bucket.EXCLUDED
        assert "exclusion" in result.reasons[0]


class TestWordBoundaries:
    """Substring matching caused real misclassifications; these lock in the fix."""

    def test_indiana_is_not_india(self, config):
        job = make_job(locations=["Indianapolis, Indiana"])
        assert classify(job, config).bucket is not Bucket.REMOTE_INDIA_ONLY

    def test_leader_does_not_match_lead(self, config):
        assert not matches_seniority("Engineering Team Leader Assistant", config)

    def test_lead_does_match_as_a_word(self, config):
        assert matches_seniority("Lead Engineer", config)


class TestTalentPool:
    def test_pooling_language_is_flagged(self, config):
        job = make_job(
            description="This posting is for active pooling purposes only and "
            "does not guarantee employment.",
            locations=["Remote - India - Remote"],
        )
        assert is_talent_pool(job, config)
        result = classify(job, config)
        assert result.is_talent_pool
        # Flagged, but still reported in its proper bucket — never dropped.
        assert result.bucket is Bucket.REMOTE_INDIA_ONLY
        assert any("talent pool" in reason for reason in result.reasons)

    def test_real_requisition_is_not_flagged(self, config):
        job = make_job(description="We are hiring a Senior Infrastructure Engineer.")
        assert not is_talent_pool(job, config)


class TestDeprioritisation:
    def test_bengaluru_is_deprioritised_but_never_dropped(self, config):
        job = make_job(locations=["Remote - India - Remote", "Bengaluru, India"])
        assert is_deprioritised(job, config)
        assert classify(job, config).bucket is not Bucket.EXCLUDED

    def test_mumbai_is_not_deprioritised(self, config):
        assert not is_deprioritised(make_job(locations=["Mumbai, India"]), config)


@pytest.mark.parametrize(
    "locations", [["Remote - India - Remote"], ["Remote"], ["Paris, France"], []]
)
def test_classification_always_explains_itself(config, locations):
    """Every classification carries reasons; an unauditable verdict is not trusted."""
    assert classify(make_job(locations=locations), config).reasons
