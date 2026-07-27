"""Shared OwO prefix helpers for server-specific OwO commands.

Team templates store the configurable OwO prefix in ``team_templates.db`` so the
same setting can be reused by boss inventory readers, boss tickets, and guided
team restore commands.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
OWO_PREFIX_DEFAULT = "w"
MAX_OWO_PREFIX_LENGTH = 5

_PREFIX_RE = re.compile(r"[A-Za-z0-9_!?$./~#%&+\-=]{1,5}")


def normalize_owo_prefix(value: str | None) -> str | None:
    """Return a safe lowercase OwO prefix, or ``None`` for invalid input."""
    prefix = re.sub(r"\s+", "", value or "").strip()
    if not prefix or len(prefix) > MAX_OWO_PREFIX_LENGTH:
        return None
    if not _PREFIX_RE.fullmatch(prefix):
        return None
    return prefix.lower()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def get_guild_owo_prefix_sync(guild_id: int | None) -> str:
    """Read a guild's configured OwO prefix from the team config database."""
    if not guild_id or not DATABASE_FILE.exists():
        return OWO_PREFIX_DEFAULT
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT owo_prefix FROM team_guild_config WHERE guild_id = ?",
                (int(guild_id),),
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return OWO_PREFIX_DEFAULT
    prefix = normalize_owo_prefix(str(row["owo_prefix"])) if row else OWO_PREFIX_DEFAULT
    return prefix or OWO_PREFIX_DEFAULT


async def get_guild_owo_prefix(guild_id: int | None) -> str:
    return await asyncio.to_thread(get_guild_owo_prefix_sync, guild_id)


def normalize_owo_message_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def is_owo_prefixed_command(
    content: str,
    owo_prefix: str | None,
    suffixes: set[str] | frozenset[str],
) -> bool:
    """Match ``owo <suffix>`` or the server's configured prefix + suffix.

    Examples with suffix ``bossi`` and prefix ``o``:
    - ``owo boss i`` -> true
    - ``o boss i`` -> true
    - ``w boss i`` -> false, because this server uses ``o``
    """
    normalized = normalize_owo_message_command(content)
    prefix = normalize_owo_prefix(owo_prefix) or OWO_PREFIX_DEFAULT
    return normalized in {f"owo{suffix}" for suffix in suffixes} | {f"{prefix}{suffix}" for suffix in suffixes}


def is_possible_owo_prefixed_command(
    content: str,
    suffixes: set[str] | frozenset[str],
) -> bool:
    """Conservative fallback used when the guild prefix cannot be awaited.

    This is used only after an interaction was already tied to an exact message,
    such as a nickname-marker reaction under the user's own ticket command.
    """
    normalized = normalize_owo_message_command(content)
    if any(normalized == f"owo{suffix}" for suffix in suffixes):
        return True
    for suffix in suffixes:
        if not normalized.endswith(suffix):
            continue
        prefix = normalized[: -len(suffix)]
        if normalize_owo_prefix(prefix):
            return True
    return False


def owo_command(owo_prefix: str | None, *parts: object) -> str:
    """Build a readable OwO command using the configured prefix."""
    prefix = normalize_owo_prefix(owo_prefix) or OWO_PREFIX_DEFAULT
    suffix = " ".join(str(part).strip() for part in parts if str(part).strip())
    return f"{prefix} {suffix}" if suffix else prefix
