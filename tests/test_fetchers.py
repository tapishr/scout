"""Tests for the source parsers.

Every test here runs against a captured fixture through a stub client — no test
in this file touches the network. Parsers are checked for the specific quirks
that bit during development (RemoteOK's metadata element, Greenhouse's packed
location string, Himalayas' page cap) rather than for field-by-field equality,
which would just restate the fixture.
"""

from __future__ import annotations

import pytest

from conftest import ExplodingClient, StubClient, load_fixture
from scout.fetchers import (
    SOURCES,
    aggregators,
    ashby,
    atlassian,
    greenhouse,
    smartrecruiters,
    workable,
)
from scout.runner import poll_sources


class TestAshby:
    def test_parses_locations_and_remote_flag(self):
        jobs = ashby.parse(load_fixture("ashby_board.json"), "clickhouse")
        assert jobs
        assert all(j.source == "ashby" and j.org == "clickhouse" for j in jobs)
        india = [j for j in jobs if "India" in j.locations]
        assert india, "fixture should contain an India posting"
        assert india[0].url.startswith("https://")

    def test_secondary_locations_are_included(self):
        jobs = ashby.parse(load_fixture("ashby_board.json"), "clickhouse")
        assert any(len(j.locations) > 1 for j in jobs)

    def test_unlisted_jobs_are_dropped(self):
        payload = {"jobs": [{"id": "x", "title": "Senior Engineer", "isListed": False}]}
        assert ashby.parse(payload, "org") == []

    def test_fetch_walks_every_configured_org(self, config):
        client = StubClient(default=load_fixture("ashby_board.json"))
        jobs = ashby.fetch(client, config)
        assert len(client.calls) == len(config.source("ashby")["orgs"])
        assert jobs


class TestGreenhouse:
    def test_packed_location_string_is_split(self):
        """Greenhouse packs several places into one field; a foreign one must
        not be able to mask an Indian one."""
        jobs = greenhouse.parse(load_fixture("greenhouse_board.json"), "gitlab")
        multi = [j for j in jobs if len(j.locations) > 1]
        assert multi, "fixture should contain a semicolon-packed location"

    def test_404_org_is_skipped_not_fatal(self, config):
        client = StubClient(default=None)  # None is how the client reports a 404
        assert greenhouse.fetch(client, config) == []


class TestAtlassian:
    def test_canonical_remote_india_posting_survives_parsing(self):
        jobs = atlassian.parse(load_fixture("atlassian_listings.json"))
        match = [j for j in jobs if j.external_id == "25283"]
        assert match, "req 25283 is the regression anchor for this source"
        assert "Remote - India - Remote" in match[0].locations

    def test_description_is_assembled_from_all_sections(self):
        jobs = atlassian.parse(load_fixture("atlassian_listings.json"))
        assert any(j.description for j in jobs)

    def test_non_list_payload_is_handled(self, config):
        assert atlassian.fetch(StubClient(default={"unexpected": True}), config) == []


class TestAggregators:
    def test_remoteok_metadata_element_is_skipped(self):
        """The first array element is a legal notice, not a job."""
        payload = load_fixture("remoteok.json")
        jobs = aggregators.parse_remoteok(payload)
        assert len(jobs) == len(payload) - 1
        assert all(j.title for j in jobs)

    def test_himalayas_restrictions_become_restrictions_not_locations(self):
        jobs = aggregators.parse_himalayas(load_fixture("himalayas.json"))
        assert any(j.location_restrictions for j in jobs)
        assert all(j.locations == ["Remote"] for j in jobs)

    def test_himalayas_follows_the_cursor(self, config):
        """The endpoint caps a page at 20 however large `limit` is."""
        page = load_fixture("himalayas.json")
        client = StubClient(default={**page, "nextCursor": None})
        jobs = aggregators.fetch_himalayas(client, config)
        assert jobs
        assert len(client.calls) == 1, "a null cursor must stop paging"

    def test_himalayas_stops_at_the_configured_page_limit(self, config):
        page = {**load_fixture("himalayas.json"), "nextCursor": "always-more"}
        client = StubClient(default=page)
        aggregators.fetch_himalayas(client, config)
        assert len(client.calls) == config.source("himalayas")["pages"]

    def test_remotive_required_location_is_a_restriction(self):
        jobs = aggregators.parse_remotive(load_fixture("remotive.json"))
        assert jobs and any(j.location_restrictions for j in jobs)

    def test_jobicy_geo_is_a_restriction(self):
        jobs = aggregators.parse_jobicy(load_fixture("jobicy.json"))
        assert jobs and all(j.source == "jobicy" for j in jobs)

    def test_workingnomads_parses(self):
        jobs = aggregators.parse_workingnomads(load_fixture("workingnomads.json"))
        assert jobs and all(j.url for j in jobs)


class TestWorkableAndSmartRecruiters:
    """Both returned zero rows live on 2026-10-15; fixtures are synthetic."""

    def test_workable_telecommuting_becomes_a_remote_location(self):
        jobs = workable.parse(load_fixture("workable_board.json"), "example")
        remote = [j for j in jobs if j.remote_flag]
        assert remote and "Remote" in remote[0].locations

    def test_workable_talent_pool_text_is_preserved_for_the_classifier(self, config):
        from scout.filters import is_talent_pool

        jobs = workable.parse(load_fixture("workable_board.json"), "example")
        pools = [j for j in jobs if is_talent_pool(j, config)]
        assert pools, "the pooling disclaimer must survive parsing"

    def test_smartrecruiters_builds_a_readable_location(self):
        jobs = smartrecruiters.parse(load_fixture("smartrecruiters_postings.json"), "ExampleCo")
        assert jobs
        assert any("India" in loc for j in jobs for loc in j.locations)


class TestRunOutcome:
    def test_dead_slugs_are_collected_for_reporting(self, config):
        """A 404 is not an error, but it must not vanish either."""
        from scout.runner import run
        from scout.store import SnapshotStore

        class NotFoundClient:
            dead_urls: list[str] = []

            def get_json(self, url: str, *, allow_404: bool = True):
                self.dead_urls.append(url)
                return None

        outcome = run(
            config, SnapshotStore("/tmp/scout-test-nonexistent"),
            only=["atlassian"], dry_run=True, client=NotFoundClient(),
        )
        assert outcome.dead_urls
        assert outcome.sources_ok == ["atlassian"]


class TestFailureIsolation:
    def test_one_dead_source_does_not_kill_the_run(self, config):
        """The whole point of wrapping each fetcher."""
        results = poll_sources(config, ExplodingClient())
        assert results, "every enabled source should still be attempted"
        assert all(not r.ok for r in results)
        assert all("RuntimeError" in (r.error or "") for r in results)

    def test_a_working_source_still_returns_when_another_fails(self, config):
        class HalfBroken:
            def get_json(self, url: str, *, allow_404: bool = True):
                if "atlassian" in url:
                    raise RuntimeError("atlassian is down")
                return load_fixture("ashby_board.json")

        results = poll_sources(config, HalfBroken(), only=["ashby", "atlassian"])
        by_name = {r.source: r for r in results}
        assert by_name["ashby"].ok and by_name["ashby"].jobs
        assert not by_name["atlassian"].ok

    @pytest.mark.parametrize("name", sorted(SOURCES))
    def test_every_registered_source_is_callable(self, name, config):
        assert callable(SOURCES[name])
