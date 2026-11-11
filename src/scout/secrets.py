"""Credential lookup that keeps secrets out of the public config.

``config.yaml`` is committed, so nothing sensitive may live in it. Values are
resolved in this order, first hit wins:

1. the real process environment — what CI and one-off runs use
2. ``./.env`` next to the config — gitignored, convenient for a laptop
3. ``~/.config/scout/env`` — outside the repo entirely, so it cannot be
   committed by accident even if the ignore rule is edited away

A value of the form ``keychain:<service>`` is resolved through the macOS
keychain instead of being stored anywhere in plaintext, which is the option
worth preferring on a machine you share or back up.

Secrets are never logged, never echoed, and never written back to disk.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

#: Searched in order; the first file that defines a key wins.
ENV_FILES = [Path(".env"), Path.home() / ".config" / "scout" / "env"]

_cache: dict[str, str] | None = None


def _parse(path: Path) -> dict[str, str]:
    """Read a KEY=VALUE file. Blank lines and ``#`` comments are ignored."""
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    _warn_if_world_readable(path)
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def _warn_if_world_readable(path: Path) -> None:
    """Loose permissions on a secrets file are worth one line on stderr."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(
            f"warning: {path} is readable by other users — chmod 600 it",
            file=sys.stderr,
        )


def _from_keychain(service: str) -> str | None:
    """Fetch a generic password from the macOS keychain, or None."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        merged: dict[str, str] = {}
        for path in reversed(ENV_FILES):      # later files are lower priority
            merged.update(_parse(path))
        _cache = merged
    return _cache


def get(name: str, default: str | None = None) -> str | None:
    """Resolve one credential. The process environment always wins."""
    value = os.environ.get(name) or _load().get(name) or default
    if value and value.startswith("keychain:"):
        return _from_keychain(value.split(":", 1)[1]) or default
    return value


def reset_cache() -> None:
    """Drop the parsed-file cache. For tests."""
    global _cache
    _cache = None
