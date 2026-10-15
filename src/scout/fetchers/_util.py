"""Shared parsing helpers for fetchers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def parse_timestamp(value: object) -> datetime | None:
    """Parse the several date shapes job boards emit, returning UTC or ``None``.

    Boards are inconsistent (ISO with ``Z``, ISO with offset, epoch seconds,
    ``YYYY-MM-DD HH:MM AM``). A date we cannot parse must not fail a run — the
    posting is still useful without its age.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    normalised = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        formats = (
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%a, %d %b %Y %H:%M:%S %z",
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def strip_html(value: str | None, limit: int = 2000) -> str:
    """Flatten HTML to text for talent-pool detection.

    Truncated because only the opening of a description carries the "this is a
    pool, not a req" disclaimer, and whole descriptions would bloat memory for
    thousands of postings.
    """
    if not value:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", value)).strip()[:limit]


def split_locations(value: str | None) -> list[str]:
    """Split a board's single location string into separate places.

    Greenhouse packs several into one field (``"Remote, Canada; Remote, United
    Kingdom"``); treating that as one string would let a foreign location mask
    an Indian one.
    """
    if not value:
        return []
    parts = re.split(r"\s*(?:;|\band\b|\||/)\s*", value)
    return [p.strip() for p in parts if p and p.strip()]
