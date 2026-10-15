"""Typed configuration loading.

Config is a file, never code, because keyword lists get tuned far more often
than logic does and a config edit needs no test run. Loading validates eagerly
so a typo surfaces at startup rather than as a silently empty result set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(ValueError):
    """Raised when the config file is missing required structure."""


@dataclass
class HttpConfig:
    timeout_seconds: float = 20.0
    delay_seconds: float = 1.0
    max_retries: int = 2
    backoff_seconds: float = 2.0
    user_agent: str = "scout-job-discovery/0.1 (+https://github.com/tapishr/scout)"


@dataclass
class MatchingConfig:
    seniority_keywords: list[str] = field(default_factory=list)
    role_keywords: list[str] = field(default_factory=list)
    title_exclusions: list[str] = field(default_factory=list)


@dataclass
class GeographyConfig:
    india_terms: list[str] = field(default_factory=list)
    india_office_cities: list[str] = field(default_factory=list)
    deprioritised_cities: list[str] = field(default_factory=list)
    remote_terms: list[str] = field(default_factory=list)
    excluding_regions: list[str] = field(default_factory=list)


@dataclass
class Config:
    http: HttpConfig
    matching: MatchingConfig
    geography: GeographyConfig
    talent_pool_markers: list[str]
    sources: dict[str, dict[str, Any]]
    output: dict[str, Any] = field(default_factory=dict)

    def source(self, name: str) -> dict[str, Any]:
        """Settings for one source, or an empty mapping when absent."""
        return self.sources.get(name) or {}

    def source_enabled(self, name: str) -> bool:
        return bool(self.source(name).get("enabled", False))

    @property
    def enabled_sources(self) -> list[str]:
        return [name for name in self.sources if self.source_enabled(name)]


def _lower_list(raw: Any, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"{label} must be a list, got {type(raw).__name__}")
    return [str(item).strip().lower() for item in raw if str(item).strip()]


def find_config(explicit: str | Path | None = None) -> Path:
    """Locate the config file.

    Search order: an explicit path, ``./config.yaml``, then the copy shipped
    beside the installed package — so the tool works both from a checkout and
    from an installed console script.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    candidates = [
        Path.cwd() / DEFAULT_CONFIG_NAME,
        Path(__file__).resolve().parent.parent.parent / DEFAULT_CONFIG_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigError(
        "no config.yaml found; looked in " + ", ".join(str(c) for c in candidates)
    )


def load_config(path: str | Path | None = None) -> Config:
    """Read and validate the config file."""
    config_path = find_config(path)
    with open(config_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at the top level")

    sources = data.get("sources") or {}
    if not isinstance(sources, dict) or not sources:
        raise ConfigError(f"{config_path}: 'sources' must be a non-empty mapping")

    matching_raw = data.get("matching") or {}
    geo_raw = data.get("geography") or {}
    if not _lower_list(matching_raw.get("role_keywords"), "matching.role_keywords"):
        raise ConfigError("matching.role_keywords must not be empty; nothing would ever match")

    return Config(
        http=HttpConfig(**(data.get("http") or {})),
        matching=MatchingConfig(
            seniority_keywords=_lower_list(
                matching_raw.get("seniority_keywords"), "matching.seniority_keywords"
            ),
            role_keywords=_lower_list(matching_raw.get("role_keywords"), "matching.role_keywords"),
            title_exclusions=_lower_list(
                matching_raw.get("title_exclusions"), "matching.title_exclusions"
            ),
        ),
        geography=GeographyConfig(
            india_terms=_lower_list(geo_raw.get("india_terms"), "geography.india_terms"),
            india_office_cities=_lower_list(
                geo_raw.get("india_office_cities"), "geography.india_office_cities"
            ),
            deprioritised_cities=_lower_list(
                geo_raw.get("deprioritised_cities"), "geography.deprioritised_cities"
            ),
            remote_terms=_lower_list(geo_raw.get("remote_terms"), "geography.remote_terms"),
            excluding_regions=_lower_list(
                geo_raw.get("excluding_regions"), "geography.excluding_regions"
            ),
        ),
        talent_pool_markers=_lower_list(data.get("talent_pool_markers"), "talent_pool_markers"),
        sources=sources,
        output=data.get("output") or {},
    )
