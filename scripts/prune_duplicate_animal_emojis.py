"""Plan/back up, then explicitly delete only superseded AN_*_v3 emojis.

Run without --apply first, using a fresh --backup-dir. Review manifest.json,
then use the same directory with --apply. Never deletes sole icons, non-animal
assets, manual overrides, or an ID still used by any active logical key.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import aiohttp
import discord
from dotenv import load_dotenv

from cogs.emoji_assets import EmojiOverrideStore, override_name, versioned_name
from cogs.emoji_catalog import effective_override
from cogs.ui_emojis import DEX_ARTWORK, clear_emoji_catalog_cache, deployed_emoji_name, discover_emoji_assets


def plan_duplicates(items, overrides):
    by_name = {item.name: item for item in items}
    desired = {}
    for key in discover_emoji_assets():
        override = effective_override(overrides, key)
        desired[key] = override_name(override.key, override.image) if override else deployed_emoji_name(key)
    protected = {item.emoji_id for item in overrides.values()}
    protected.update(by_name[name].id for name in desired.values() if name in by_name)
    candidates, retained = [], []
    for key in discover_emoji_assets():
        if not key.startswith("pet_"):
            continue
        old = by_name.get(versioned_name(key, "v3"))
        if old is None:
            continue
        replacement = by_name.get(desired[key])
        if (old.id in protected or replacement is None or replacement.id == old.id
                or replacement.name.endswith("_v3")):
            retained.append({"key": key, "name": old.name, "id": old.id})
            continue
        assert old.name.startswith("AN_") and old.name.endswith("_v3")
        candidates.append({"key": key, "name": old.name, "id": old.id,
                           "animated": bool(old.animated), "replacement_name": replacement.name,
                           "replacement_id": replacement.id})
    return candidates, retained


async def run(args):
    load_dotenv(PROJECT_ROOT / ".env")
    store = EmojiOverrideStore(PROJECT_ROOT / "emoji_overrides.db")
    DEX_ARTWORK.update(store.dex_all())
    clear_emoji_catalog_cache()
    async with discord.Client(intents=discord.Intents.none(), application_id=args.application_id) as client:
        await client.login(os.environ["DISCORD_TOKEN"])
        assert (await client.application_info()).id == args.application_id
        items = await client.fetch_application_emojis()
        candidates, retained = plan_duplicates(items, store.all())
        directory = args.backup_dir.resolve()
        if not args.apply:
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            manifest = {"application_id": args.application_id, "inventory_count": len(items),
                        "candidates": candidates, "retained": retained, "images": {}}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                for index, item in enumerate(candidates, 1):
                    filename = str(item["id"]) + (".gif" if item["animated"] else ".png")
                    url = f"https://cdn.discordapp.com/emojis/{filename}?size=128&quality=lossless"
                    async with session.get(url, allow_redirects=False) as response:
                        response.raise_for_status()
                        raw = await response.read()
                    if not raw or len(raw) > 2 * 1024 * 1024:
                        raise ValueError("Invalid backup artwork size; nothing was deleted")
                    (directory / filename).write_bytes(raw)
                    manifest["images"][str(item["id"])] = {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest()}
                    if index % 20 == 0:
                        print(f"Backed up {index}/{len(candidates)} planned animal duplicates", flush=True)
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(f"PLAN: {len(candidates)} duplicates, {len(retained)} retained; {directory / 'manifest.json'}", flush=True)
            return
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["application_id"] != args.application_id or manifest["candidates"] != candidates:
            raise ValueError("Live targets or replacements changed. Generate and review a new backup plan.")
        journal = directory / "deleted.jsonl"
        if journal.exists():
            raise ValueError("This plan was already applied or partially applied; inspect its deletion journal.")
        # Validate the entire backup before deleting anything.
        for item in candidates:
            saved = manifest["images"][str(item["id"])]
            filename = str(item["id"]) + (".gif" if item["animated"] else ".png")
            if saved["filename"] != filename or hashlib.sha256((directory / filename).read_bytes()).hexdigest() != saved["sha256"]:
                raise ValueError("Backup validation failed; nothing was deleted")
        for index, item in enumerate(candidates, 1):
            old = await client.fetch_application_emoji(item["id"])
            replacement = await client.fetch_application_emoji(item["replacement_id"])
            if old.name != item["name"] or replacement.name != item["replacement_name"] or not old.is_application_owned():
                raise ValueError("Emoji identity changed; stopped cleanup")
            # Catch owner changes made since the initial plan validation.
            fresh_candidates, _ = plan_duplicates([old, replacement], store.all())
            if item not in fresh_candidates:
                raise ValueError("The target became protected; stopped cleanup")
            await old.delete()
            with journal.open("a", encoding="utf-8") as output:
                output.write(json.dumps(item) + "\n")
            if index % 10 == 0:
                print(f"Deleted {index}/{len(candidates)} verified animal duplicates", flush=True)
        print(f"FINISHED: deleted {len(candidates)} animal V3 duplicates; retained {len(retained)} nonduplicates", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True, type=int)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Delete the exact previously backed-up and reviewed targets")
    asyncio.run(run(parser.parse_args()))
