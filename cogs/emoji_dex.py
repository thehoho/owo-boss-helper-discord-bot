"""Bounded imports of the original emoji artwork already saved in Animal Dex."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlsplit

import aiohttp

from .emoji_assets import MAX_UPLOAD_BYTES
from .emoji_catalog import eligible_dex_record, is_catalog_emoji
from .ui_emojis import DEX_ARTWORK, discover_emoji_assets

logger = logging.getLogger(__name__)


def dex_source_url(record) -> str | None:
    if record.source != "owo":
        return None
    parsed = urlsplit(record.image_url)
    match = re.fullmatch(r"/emojis/([0-9]{17,20})\.(png|gif|webp)", parsed.path)
    if parsed.scheme == "https" and parsed.netloc == "cdn.discordapp.com" and match:
        emoji_id, extension = match.groups()
    elif record.emoji_id and re.fullmatch(r"[0-9]{17,20}", str(record.emoji_id)):
        emoji_id, extension = str(record.emoji_id), "webp"
    else:
        return None
    if record.emoji_animated:
        extension = "gif"
    return f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=128&quality=lossless"


async def sync_dex_artwork(bot, manager) -> dict:
    await manager.ensure_synced()
    dex = getattr(bot, "animal_dex_store", None)
    if dex is None:
        raise ValueError("Animal Dex storage is not ready.")
    records = await asyncio.to_thread(dex.all_records)
    report = {"imported": 0, "reused": 0, "skipped_outside_catalog": 0, "missing": [], "failed": []}
    candidates = {}
    for record in records:
        if not eligible_dex_record(record):
            report["skipped_outside_catalog"] += 1
            continue
        key = "pet_" + record.animal_key
        if not re.fullmatch(r"pet_[a-z0-9_]{1,59}", key):
            report["failed"].append(f"{key}: unsupported key")
            continue
        candidates[key] = record
    report["missing"] = sorted(key for key in set(candidates) | {k for k in discover_emoji_assets() if k.startswith("pet_") and is_catalog_emoji(k)}
                               if key not in candidates or not dex_source_url(candidates[key]))
    inventory = await bot.fetch_application_emojis()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        for index, (key, record) in enumerate(candidates.items()):
            url = dex_source_url(record)
            if not url:
                continue
            if key in DEX_ARTWORK and DEX_ARTWORK[key][3] == url:
                report["reused"] += 1
                continue
            try:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError(f"CDN status {response.status}")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_UPLOAD_BYTES:
                            raise ValueError("Source exceeds 2 MiB")
                await manager.install_dex_asset(key, bytes(raw), record.display_name, json.dumps(record.aliases), url, inventory)
                report["imported"] += 1
                from .emoji_tools import reference_entries
                reference_entries.cache_clear()
            except Exception as exc:
                report["failed"].append(f"{key}: {type(exc).__name__}: {str(exc)[:160]}")
                logger.warning("Dex artwork import failed for %s: %s", key, type(exc).__name__)
            if (report["imported"] + len(report["failed"])) % 25 == 0:
                logger.info("Dex artwork progress: %s imported, %s reused, %s failed; record %s/%s",
                            report["imported"], report["reused"], len(report["failed"]), index + 1, len(candidates))
    logger.info("Dex artwork sync finished: %s imported, %s reused, %s need Dex sources, %s failed",
                report["imported"], report["reused"], len(report["missing"]), len(report["failed"]))
    return report
