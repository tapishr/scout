"""Tests for config loading and validation."""

from __future__ import annotations

import pytest

from scout.config import ConfigError, load_config


def write(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestShippedConfig:
    def test_repo_config_loads(self, config):
        assert config.enabled_sources
        assert config.matching.role_keywords
        assert config.geography.india_terms

    def test_every_enabled_source_has_a_fetcher(self, config):
        from scout.fetchers import SOURCES

        assert set(config.enabled_sources) <= set(SOURCES)

    def test_keywords_are_normalised_to_lowercase(self, config):
        assert all(k == k.lower() for k in config.matching.seniority_keywords)


class TestValidation:
    def test_missing_file_is_a_clear_error(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "absent.yaml")

    def test_sources_must_not_be_empty(self, tmp_path):
        path = write(tmp_path, "matching:\n  role_keywords: [engineer]\nsources: {}\n")
        with pytest.raises(ConfigError, match="sources"):
            load_config(path)

    def test_empty_role_keywords_is_rejected(self, tmp_path):
        """Nothing would ever match, so fail loudly instead of reporting zero."""
        path = write(
            tmp_path,
            "matching:\n  role_keywords: []\nsources:\n  ashby:\n    enabled: true\n",
        )
        with pytest.raises(ConfigError, match="role_keywords"):
            load_config(path)

    def test_non_list_keyword_block_is_rejected(self, tmp_path):
        path = write(
            tmp_path,
            "matching:\n  role_keywords: [engineer]\n  seniority_keywords: senior\n"
            "sources:\n  ashby:\n    enabled: true\n",
        )
        with pytest.raises(ConfigError, match="must be a list"):
            load_config(path)

    def test_disabled_source_is_not_enabled(self, tmp_path):
        path = write(
            tmp_path,
            "matching:\n  role_keywords: [engineer]\nsources:\n"
            "  ashby:\n    enabled: true\n  workable:\n    enabled: false\n",
        )
        assert load_config(path).enabled_sources == ["ashby"]
