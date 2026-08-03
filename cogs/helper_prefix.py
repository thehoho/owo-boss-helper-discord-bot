"""Shared per-server prefix helpers for OwO Boss Helper commands.

The helper prefix is intentionally separate from the OwO command prefix. Both
settings live in ``team_templates.db`` so deployments do not introduce another
runtime file that must be backed up.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
HELPER_PREFIX_DEFAULT = "h"
MAX_HELPER_PREFIX_LENGTH = 5

_PREFIX_RE = re.compile(r"[A-Za-z0-9_!?$./~#%&+\-=]{1,5}")
_PREFIX_CACHE: dict[int, str] = {}


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then release the SQLite file handle."""

    def __enter__(self) -> "ClosingConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def normalize_helper_prefix(value: str | None) -> str | None:
    """Return a safe lowercase helper prefix, or ``None`` when invalid."""
    prefix = re.sub(r"\s+", "", value or "").strip()
    if not prefix or len(prefix) > MAX_HELPER_PREFIX_LENGTH:
        return None
    if not _PREFIX_RE.fullmatch(prefix):
        return None
    return prefix.lower()


def helper_command(helper_prefix: str | None, *parts: object) -> str:
    """Build a readable helper command from the configured prefix."""
    prefix = normalize_helper_prefix(helper_prefix) or HELPER_PREFIX_DEFAULT
    suffix = " ".join(str(part).strip() for part in parts if str(part).strip())
    return f"{prefix} {suffix}" if suffix else prefix


def helper_alias(helper_prefix: str | None, default_alias: str) -> str:
    """Replace the leading ``H`` in a historical alias with a guild prefix."""
    alias = (default_alias or "").casefold()
    if not alias.startswith(HELPER_PREFIX_DEFAULT):
        raise ValueError(f"Helper alias must start with H: {default_alias!r}")
    prefix = normalize_helper_prefix(helper_prefix) or HELPER_PREFIX_DEFAULT
    return f"{prefix}{alias[1:]}"


def helper_aliases(
    helper_prefix: str | None,
    default_aliases: tuple[str, ...] | set[str] | frozenset[str],
) -> tuple[str, ...]:
    return tuple(helper_alias(helper_prefix, alias) for alias in default_aliases)


def canonicalize_helper_command(
    content: str,
    helper_prefix: str | None,
) -> str:
    """Rewrite a guild prefix to historical ``h`` for legacy parsers."""
    lines = (content or "").splitlines()
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return content or ""
    original = lines[first_index]
    stripped = original.strip()
    prefix = normalize_helper_prefix(helper_prefix) or HELPER_PREFIX_DEFAULT
    if not stripped.casefold().startswith(prefix.casefold()):
        # Legacy parsers still recognize historical H aliases after rewriting.
        # Once a guild chooses another prefix, suppress those old aliases instead
        # of returning them unchanged and accidentally keeping both prefixes live.
        if (
            prefix != HELPER_PREFIX_DEFAULT
            and stripped.casefold().startswith(HELPER_PREFIX_DEFAULT)
        ):
            return ""
        return content or ""
    leading = original[: len(original) - len(original.lstrip())]
    lines[first_index] = f"{leading}{HELPER_PREFIX_DEFAULT}{stripped[len(prefix):]}"
    return "\n".join(lines)


def compact_first_line(content: str) -> str:
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    return re.sub(r"\s+", "", first_line).casefold()


def matches_helper_command(
    content: str,
    helper_prefix: str | None,
    default_aliases: tuple[str, ...] | set[str] | frozenset[str],
) -> bool:
    compact = compact_first_line(content)
    return compact in {
        re.sub(r"\s+", "", alias).casefold()
        for alias in helper_aliases(helper_prefix, default_aliases)
    }


def parse_helper_command_argument(
    content: str,
    helper_prefix: str | None,
    default_aliases: tuple[str, ...] | set[str] | frozenset[str],
) -> str | None:
    """Return text following an exact helper alias, empty text, or ``None``."""
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    text = re.sub(r"\s+", " ", first_line).strip()
    lowered = text.casefold()
    aliases = sorted(
        helper_aliases(helper_prefix, default_aliases),
        key=len,
        reverse=True,
    )
    for alias in aliases:
        if lowered == alias:
            return ""
        if lowered.startswith(f"{alias} "):
            return text[len(alias) :].strip()
    return None


def _connect() -> ClosingConnection:
    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=10,
        factory=ClosingConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_guild_config (
            guild_id INTEGER PRIMARY KEY,
            owo_prefix TEXT NOT NULL DEFAULT 'w',
            helper_prefix TEXT NOT NULL DEFAULT 'h',
            updated_by INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(team_guild_config)")
    }
    if "helper_prefix" not in columns:
        connection.execute(
            "ALTER TABLE team_guild_config "
            "ADD COLUMN helper_prefix TEXT NOT NULL DEFAULT 'h'"
        )


def get_guild_helper_prefix_sync(guild_id: int | None) -> str:
    if not guild_id or not DATABASE_FILE.exists():
        return HELPER_PREFIX_DEFAULT
    try:
        with _connect() as connection:
            _ensure_schema(connection)
            row = connection.execute(
                "SELECT helper_prefix FROM team_guild_config WHERE guild_id = ?",
                (int(guild_id),),
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return HELPER_PREFIX_DEFAULT
    prefix = (
        normalize_helper_prefix(str(row["helper_prefix"]))
        if row
        else HELPER_PREFIX_DEFAULT
    )
    return prefix or HELPER_PREFIX_DEFAULT


async def get_guild_helper_prefix(guild_id: int | None) -> str:
    if not guild_id:
        return HELPER_PREFIX_DEFAULT
    cached = _PREFIX_CACHE.get(int(guild_id))
    if cached is not None:
        return cached
    prefix = await asyncio.to_thread(get_guild_helper_prefix_sync, guild_id)
    _PREFIX_CACHE[int(guild_id)] = prefix
    return prefix


def set_guild_helper_prefix_sync(
    guild_id: int,
    helper_prefix: str,
    updated_by: int,
) -> str:
    prefix = normalize_helper_prefix(helper_prefix)
    if prefix is None:
        raise ValueError("Invalid helper prefix")
    now = int(time.time())
    with _connect() as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO team_guild_config (
                guild_id, helper_prefix, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                helper_prefix = excluded.helper_prefix,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (int(guild_id), prefix, int(updated_by), now),
        )
    return prefix


async def set_guild_helper_prefix(
    guild_id: int,
    helper_prefix: str,
    updated_by: int,
) -> str:
    saved = await asyncio.to_thread(
        set_guild_helper_prefix_sync,
        guild_id,
        helper_prefix,
        updated_by,
    )
    _PREFIX_CACHE[int(guild_id)] = saved
    return saved
