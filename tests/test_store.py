"""Tests for snapshot persistence and run-to-run diffing."""

from __future__ import annotations

from datetime import datetime, timezone

from scout.models import Bucket, Classification, Job, ScoutedJob, Snapshot
from scout.store import SnapshotStore, build_snapshot, diff


def job(source="ashby", org="clickhouse", external_id="1", title="Senior SRE", **kwargs) -> Job:
    return Job(
        source=source,
        org=org,
        external_id=external_id,
        title=title,
        company=kwargs.pop("company", "ClickHouse"),
        url="https://example.invalid/j",
        **kwargs,
    )


def scouted(j: Job, bucket=Bucket.REMOTE_INDIA_ONLY) -> ScoutedJob:
    return ScoutedJob(
        job=j,
        classification=Classification(bucket=bucket, seniority_match=True, role_match=True),
    )


class TestDiff:
    def test_first_run_reports_everything(self):
        current = [scouted(job(external_id="1")), scouted(job(external_id="2"))]
        result = diff(None, current, sources_ok=["ashby"])
        assert result.is_first_run
        assert len(result.new) == 2
        assert result.disappeared == []

    def test_only_unseen_roles_are_new(self):
        previous = build_snapshot([job(external_id="1")], ["ashby"], {})
        current = [scouted(job(external_id="1")), scouted(job(external_id="2"))]
        result = diff(previous, current, sources_ok=["ashby"])
        assert [s.job.external_id for s in result.new] == ["2"]
        assert [s.job.external_id for s in result.carried_over] == ["1"]

    def test_pulled_requisition_is_reported_as_disappeared(self):
        """Two of the operator's five applications had their reqs pulled."""
        previous = build_snapshot([job(external_id="1"), job(external_id="2")], ["ashby"], {})
        result = diff(previous, [scouted(job(external_id="1"))], sources_ok=["ashby"])
        assert [j.external_id for j in result.disappeared] == ["2"]

    def test_failed_source_does_not_fake_disappearances(self):
        """The trap this guard exists for: one flaky API must not claim every
        role it used to return has been pulled."""
        previous = build_snapshot(
            [job(source="ashby", external_id="1"), job(source="remoteok", external_id="9")],
            ["ashby", "remoteok"],
            {},
        )
        # remoteok failed this run, so it reported no jobs at all.
        result = diff(previous, [], sources_ok=["ashby"])
        assert [j.external_id for j in result.disappeared] == ["1"]
        assert result.skipped_sources == ["remoteok"]

    def test_edited_posting_is_not_reported_as_new(self):
        """Identity excludes mutable fields, so a salary edit is not 'new'."""
        previous = build_snapshot([job(external_id="1", compensation="$100k")], ["ashby"], {})
        current = [scouted(job(external_id="1", compensation="$200k"))]
        result = diff(previous, current, sources_ok=["ashby"])
        assert result.new == []


class TestIdentity:
    def test_key_is_stable_across_runs(self):
        assert job(external_id="42").key == job(external_id="42").key

    def test_key_falls_back_to_content_hash_without_an_id(self):
        a = job(external_id="", locations=["Remote - India - Remote"])
        b = job(external_id="", locations=["Remote - India - Remote"])
        assert a.key == b.key
        assert a.key.startswith("ashby:clickhouse:")

    def test_different_sources_do_not_collide(self):
        assert job(source="ashby").key != job(source="greenhouse").key


class TestPersistence:
    def test_snapshot_round_trips(self, tmp_path):
        store = SnapshotStore(tmp_path)
        original = build_snapshot(
            [job(posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc), locations=["India"])],
            ["ashby"],
            {"remoteok": "boom"},
        )
        store.save(original)
        loaded = store.load_latest()
        assert loaded is not None
        assert loaded.jobs[0].key == original.jobs[0].key
        assert loaded.jobs[0].locations == ["India"]
        assert loaded.sources_failed == {"remoteok": "boom"}

    def test_missing_state_dir_is_a_first_run_not_a_crash(self, tmp_path):
        assert SnapshotStore(tmp_path / "nope").load_latest() is None

    def test_corrupt_snapshot_is_skipped_rather_than_fatal(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.save(build_snapshot([job(external_id="1")], ["ashby"], {}))
        bad = store.runs_dir / "99999999T999999Z.json"
        bad.write_text("{ not json")
        loaded = store.load_latest()
        assert loaded is not None and loaded.jobs[0].external_id == "1"

    def test_prune_keeps_the_newest(self, tmp_path):
        store = SnapshotStore(tmp_path)
        for day in range(1, 6):
            snap = Snapshot(run_at=datetime(2026, 10, day, tzinfo=timezone.utc), jobs=[])
            store.save(snap)
        assert len(store.snapshot_paths()) == 5
        assert store.prune(keep=2) == 3
        assert len(store.snapshot_paths()) == 2
