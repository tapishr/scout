"""Source registry.

Each entry maps a config key to a callable ``(client, config) -> list[Job]``.
Adding a board means adding a module and one line here; nothing downstream
changes, because fetchers do no filtering — they only normalise to
:class:`~scout.models.Job`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Config
from ..http import PoliteClient
from ..models import Job
from . import aggregators, ashby, atlassian, email_alerts, greenhouse, smartrecruiters, workable

FetchFn = Callable[[PoliteClient, Config], list[Job]]

SOURCES: dict[str, FetchFn] = {
    "ashby": ashby.fetch,
    "greenhouse": greenhouse.fetch,
    "workable": workable.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "atlassian": atlassian.fetch,
    "email": email_alerts.fetch,
    "himalayas": aggregators.fetch_himalayas,
    "remoteok": aggregators.fetch_remoteok,
    "remotive": aggregators.fetch_remotive,
    "workingnomads": aggregators.fetch_workingnomads,
    "jobicy": aggregators.fetch_jobicy,
}

__all__ = ["SOURCES", "FetchFn"]
