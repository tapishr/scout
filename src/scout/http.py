"""The single HTTP entry point for every fetcher.

Centralised so politeness is structural rather than per-fetcher discipline:
every call gets a timeout, a delay, a retry ceiling and an identifying
User-Agent. These are free public APIs; being a bad citizen gets the tool
blocked, which costs the operator the thing it exists to provide.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import HttpConfig

log = logging.getLogger(__name__)

#: Status codes worth a retry. 4xx is never retried — the request is wrong and
#: repeating it just hammers someone else's server.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A source could not be read. Caught per-source so one failure is contained."""


class PoliteClient:
    """A thin requests wrapper enforcing timeout, delay and bounded retries."""

    def __init__(self, config: HttpConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": config.user_agent, "Accept": "application/json"}
        )
        self._last_call_at: float = 0.0
        #: URLs that 404'd this run. A dead org slug is silent by nature — the
        #: board simply vanishes from the results — so it is collected here and
        #: surfaced in the digest rather than left in a verbose log nobody reads.
        self.dead_urls: list[str] = []

    def _wait_turn(self) -> None:
        """Sleep so consecutive calls are at least ``delay_seconds`` apart."""
        if self._config.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_call_at
        remaining = self._config.delay_seconds - elapsed
        if self._last_call_at and remaining > 0:
            time.sleep(remaining)

    def get_json(self, url: str, *, allow_404: bool = True) -> Any:
        """GET and parse JSON.

        A 404 returns ``None`` when ``allow_404`` is set, because a dead org slug
        is a config problem to report, not a run-ending error. Anything else that
        fails after the retry budget raises :class:`FetchError`.
        """
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            self._wait_turn()
            try:
                response = self._session.get(url, timeout=self._config.timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
                log.debug("GET %s failed (attempt %s/%s): %s", url, attempt + 1, attempts, exc)
            else:
                self._last_call_at = time.monotonic()
                if response.status_code == 404 and allow_404:
                    log.warning("404 (check the slug): %s", url)
                    self.dead_urls.append(url)
                    return None
                if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
                    last_error = FetchError(f"HTTP {response.status_code} from {url}")
                    log.debug("retryable %s from %s", response.status_code, url)
                elif not response.ok:
                    raise FetchError(f"HTTP {response.status_code} from {url}")
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise FetchError(f"non-JSON response from {url}: {exc}") from exc
            finally:
                self._last_call_at = time.monotonic()

            if attempt < attempts - 1:
                time.sleep(self._config.backoff_seconds * (2**attempt))

        raise FetchError(f"giving up on {url} after {attempts} attempts: {last_error}")
