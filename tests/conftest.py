"""Shared test fixtures.

Fixture payloads under ``tests/fixtures/`` are trimmed captures of real API
responses, except ``workable_board.json`` and ``smartrecruiters_postings.json``,
which are synthetic: both APIs returned zero rows for every org tried on
2026-10-15, so those two follow the documented response shape instead.

No test in this suite touches the network. The fetchers' network layer is
exercised through a stub client; the logic worth testing — classification and
diffing — is pure and needs no I/O at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_fixture(name: str):
    """Read a fixture payload by filename."""
    with open(FIXTURES / name, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def config():
    """The repo's real config.yaml, so tests fail if shipped config drifts."""
    return load_config(REPO_ROOT / "config.yaml")


class StubClient:
    """Stands in for :class:`scout.http.PoliteClient`.

    Maps URL substrings to payloads so a fetcher's paging and URL construction
    are exercised without a network. Any unmatched URL returns ``None``, which
    is how the real client reports a 404.
    """

    def __init__(self, routes: dict[str, object] | None = None, default=None):
        self.routes = routes or {}
        self.default = default
        self.calls: list[str] = []

    def get_json(self, url: str, *, allow_404: bool = True):
        self.calls.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                return payload
        return self.default


class ExplodingClient:
    """A client that always raises, to prove one dead source cannot kill a run."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError("source is down")

    def get_json(self, url: str, *, allow_404: bool = True):
        raise self.exc
