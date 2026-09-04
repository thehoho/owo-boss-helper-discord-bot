"""Validated emoji uploads and durable application-emoji overrides."""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import time
import warnings
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 2048 * 2048
MAX_EMOJI_BYTES = 256 * 1024
EMOJI_PREFIXES = {"passive": "PS", "weapon": "W", "stat": "ST", "pet": "AN", "rank": "R"}


def emoji_label(key: str) -> str:
    category, _, stem = key.partition("_")
    return f"{EMOJI_PREFIXES[category]}_{stem}" if category in EMOJI_PREFIXES else f"UI_{key}"


def versioned_name(key: str, suffix: str) -> str:
    label = emoji_label(key)
    if len(label) + len(suffix) + 1 > 32:
        digest = hashlib.sha256(key.encode()).hexdigest()[:6]
        label = label[:32 - len(suffix) - len(digest) - 2] + "_" + digest
    return f"{label}_{suffix}"
CUSTOM_EMOJI_RE = re.compile(r"<(?P<animated>a?):[A-Za-z0-9_]{2,32}:(?P<id>[0-9]{17,20})>")


def custom_emoji_url(value: str) -> str:
    """Accept Discord markup only; never fetch arbitrary user-supplied URLs."""
    match = CUSTOM_EMOJI_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Choose a custom Discord emoji, or attach a PNG, JPEG, WebP, or GIF image.")
    extension = "gif" if match["animated"] else "png"
    return f"https://cdn.discordapp.com/emojis/{match['id']}.{extension}?size=128&quality=lossless"


def normalize_upload(raw: bytes) -> bytes:
    """Fill a 128px canvas without inventing detail or silently flattening GIFs."""
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("The source image must be nonempty and at most 2 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("Use a PNG, JPEG, WebP, or GIF image.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Use an image with at most 4 megapixels.")
                frames = getattr(source, "n_frames", 1)
                if frames > 1:
                    if (source.format not in {"GIF", "WEBP"} or source.width > 128
                            or source.height > 128 or frames > 200
                            or len(raw) > MAX_EMOJI_BYTES):
                        raise ValueError("Animated images must be GIF/WebP, at most 128×128, 200 frames, and 256 KiB. Animation is never silently removed.")
                    for frame in range(frames):
                        source.seek(frame)
                        source.load()
                    return raw
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGBA")
                bounds = image.getchannel("A").getbbox()
                if bounds is None:
                    raise ValueError("The image is completely transparent.")
                image = image.crop(bounds)
                scale = min(128 / image.width, 128 / image.height)
                size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
                image = image.resize(size, Image.Resampling.NEAREST if scale >= 1 else Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                canvas.alpha_composite(image, ((128 - size[0]) // 2, (128 - size[1]) // 2))
                output = io.BytesIO()
                canvas.save(output, format="PNG", optimize=True)
                result = output.getvalue()
                if len(result) > MAX_EMOJI_BYTES:
                    raise ValueError("The prepared image exceeds Discord's 256 KiB limit.")
                return result
    except (OSError, EOFError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The image is invalid, truncated, or too large to process safely.") from exc


def override_name(key: str, image: bytes) -> str:
    digest = hashlib.sha256(key.encode() + b"\0" + image).hexdigest()[:12]
    return versioned_name(key, "u" + digest)


@dataclass(frozen=True)
class EmojiOverride:
    key: str
    name: str
    image: bytes
    emoji_id: int
    actor_id: int
    updated_at: int


class EmojiOverrideStore:
    """One atomic mapping per logical key; image bytes are included in backups."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS emoji_overrides (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, image BLOB NOT NULL,
                emoji_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS emoji_override_audit (
                id INTEGER PRIMARY KEY, key TEXT NOT NULL, action TEXT NOT NULL,
                emoji_id INTEGER, actor_id INTEGER NOT NULL, created_at INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS emoji_dex_assets (
                key TEXT PRIMARY KEY, image BLOB NOT NULL, display_name TEXT NOT NULL,
                aliases_json TEXT NOT NULL, source_url TEXT NOT NULL
            )""")

    def dex_all(self) -> dict[str, tuple]:
        with closing(sqlite3.connect(self.path)) as db:
            return {row[0]: row[1:] for row in db.execute("SELECT key, image, display_name, aliases_json, source_url FROM emoji_dex_assets")}

    def save_dex(self, key: str, image: bytes, display_name: str, aliases_json: str, source_url: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO emoji_dex_assets VALUES (?, ?, ?, ?, ?)",
                       (key, image, display_name, aliases_json, source_url))

    def rename(self, key: str, old: str, new: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            changed = db.execute("UPDATE emoji_overrides SET name = ? WHERE key = ? AND name = ?", (new, key, old)).rowcount
            if changed:
                db.execute("INSERT INTO emoji_override_audit (key, action, actor_id, created_at) VALUES (?, 'rename', 0, ?)", (key, int(time.time())))

    def all(self) -> dict[str, EmojiOverride]:
        with closing(sqlite3.connect(self.path)) as db:
            return {row[0]: EmojiOverride(*row) for row in db.execute(
                "SELECT key, name, image, emoji_id, actor_id, updated_at FROM emoji_overrides"
            )}

    def save(self, key: str, name: str, image: bytes, emoji_id: int, actor_id: int) -> None:
        now = int(time.time())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO emoji_overrides VALUES (?, ?, ?, ?, ?, ?)",
                       (key, name, image, emoji_id, actor_id, now))
            db.execute("INSERT INTO emoji_override_audit (key, action, emoji_id, actor_id, created_at) VALUES (?, 'replace', ?, ?, ?)",
                       (key, emoji_id, actor_id, now))

    def revision(self, key: str, default: str) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT name FROM emoji_overrides WHERE key = ?", (key,)).fetchone()
            sequence = db.execute("SELECT MAX(id) FROM emoji_override_audit WHERE key = ?", (key,)).fetchone()[0]
            name = row[0] if row else default
            return f"{name}@{sequence}" if sequence else name

    def reset(self, key: str, actor_id: int) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM emoji_overrides WHERE key = ?", (key,))
            db.execute("INSERT INTO emoji_override_audit (key, action, actor_id, created_at) VALUES (?, 'reset', ?, ?)",
                       (key, actor_id, int(time.time())))
