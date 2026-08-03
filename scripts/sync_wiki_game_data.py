"""Build the checked-in Special-animal and rank-icon catalog from OwO Bot Wiki.

This is a maintainer tool, not a production startup dependency. It deliberately
downloads a finite wiki template, writes normalized public game metadata, and
fetches 128px image variants suitable for Discord application emojis.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import certifi


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ANIMAL_ASSET_DIR = PROJECT_ROOT / "assets" / "game_emojis" / "animals"
RANK_ASSET_DIR = PROJECT_ROOT / "assets" / "game_emojis" / "ranks"
API_URL = "https://owobot.fandom.com/api.php"
SOURCE_PAGE = "https://owobot.fandom.com/wiki/All_Animals"
WEAPONS_PAGE = "https://owobot.fandom.com/wiki/Weapons"
SPECIAL_TEMPLATE = "Template:Special Animals List"
USER_AGENT = "OwOBossHelperCatalog/1.0 (+https://github.com/thehoho/owo-boss-helper-discord-bot)"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

RANK_FILES: dict[str, str] = {
    "common": "Common_(Tier).png",
    "uncommon": "Uncommon_(Tier).png",
    "rare": "Rare_(Tier).png",
    "epic": "Epic_(Tier).png",
    "mythical": "Mythical_(Tier).png",
    "patreon": "Patreon_(Tier).png",
    "custom_patreon": "Custom_Patreon_(Tier).gif",
    "gem": "Gem_(Tier).gif",
    "legendary": "Legendary_(Tier).gif",
    "fabled": "Fabled_(Tier).gif",
    "bot": "Bot(Tier).gif",
    "hidden": "Hidden_(Tier).gif",
    "distorted": "Distorted_(Tier).gif",
    "special": "Special_(Tier).png",
}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
        return response.read()


def api(params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    return json.loads(fetch_bytes(f"{API_URL}?{query}"))


def fetch_wikitext(page: str) -> str:
    payload = api({"action": "parse", "page": page, "prop": "wikitext"})
    return str(payload["parse"]["wikitext"])


def clean_wiki_text(value: str) -> str:
    value = re.sub(
        r"\{\{Stats Infoicon\|name=([a-z]+).*?\}\}",
        lambda match: match.group(1).upper(),
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\{\{(?:Effect|Weapons) Infoicon\|(?:effect|type)=([^|}]+).*?\}\}",
        lambda match: match.group(1).replace("_", " "),
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{.*?\}\}", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", value).strip()


def normalize_alias(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"[^a-z0-9_ -]+", "", value)
    return re.sub(r"[\s-]+", "_", value).strip("_")


def emoji_stem(name: str, used: set[str]) -> str:
    base = normalize_alias(name) or "animal"
    # UIEmojiManager adds "pet_"; Discord emoji names are limited to 32 chars.
    base = base[:28]
    candidate = base
    if candidate in used:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        candidate = f"{base[:21]}_{digest}"
    used.add(candidate)
    return candidate


def row_cells(row: str) -> list[str]:
    cells: list[str] = []
    for line in row.splitlines():
        match = re.match(r'^\|\s*align="[^"]+"\s*\|(.*)$', line.strip())
        if match:
            cells.append(match.group(1).strip())
    return cells


def parse_special_animals(wikitext: str) -> list[dict[str, Any]]:
    animals: list[dict[str, Any]] = []
    used_stems: set[str] = set()
    for raw_row in re.split(r"(?m)^\|-\s*$", wikitext)[1:]:
        cells = row_cells(raw_row)
        if len(cells) < 13:
            continue
        image_match = re.search(r"\[\[Image:([^\]|]+)", cells[0], re.IGNORECASE)
        name_match = re.search(r"\[\[([^\]|]+)", cells[1])
        if not image_match or not name_match:
            continue

        name = clean_wiki_text(name_match.group(1))
        aliases = [clean_wiki_text(item) for item in re.findall(r"\[\[([^\]|]+)", cells[12])]
        aliases = list(dict.fromkeys(item for item in [name, *aliases] if item))
        stats: list[int | None] = []
        for cell in cells[6:12]:
            number = re.search(r"-?\d+", clean_wiki_text(cell).replace(",", ""))
            stats.append(int(number.group(0)) if number else None)

        animals.append(
            {
                "key": normalize_alias(name),
                "name": name,
                "rank": "special",
                "emoji_stem": emoji_stem(name, used_stems),
                "source_image": image_match.group(1),
                "event": clean_wiki_text(cells[2]),
                "dates": clean_wiki_text(cells[3]),
                "rarity": clean_wiki_text(cells[4]),
                "caught": clean_wiki_text(cells[5]),
                "stats": {
                    "hp": stats[0],
                    "str": stats[1],
                    "pr": stats[2],
                    "wp": stats[3],
                    "mag": stats[4],
                    "mr": stats[5],
                },
                "aliases": aliases,
            }
        )
    return animals


def table_cells(row: str) -> list[str]:
    cells: list[str] = []
    for line in row.splitlines():
        stripped = line.strip()
        match = re.match(r'^\|\s*(?:align="[^"]*"\s*)?(?:style="[^"]*"\s*)?\|(.*)$', stripped)
        if match:
            cells.append(match.group(1).strip())
            continue
        if stripped.startswith("|") and not stripped.startswith("|}"):
            cells.append(stripped[1:].strip())
            continue
        if cells and stripped:
            cells[-1] += " " + stripped
    return cells


def section(wikitext: str, heading: str, next_heading: str) -> str:
    start_match = re.search(rf"(?im)^==\s*{re.escape(heading)}\s*==\s*$", wikitext)
    if not start_match:
        return ""
    end_match = re.search(
        rf"(?im)^==\s*{re.escape(next_heading)}\s*==\s*$",
        wikitext[start_match.end() :],
    )
    end = start_match.end() + end_match.start() if end_match else len(wikitext)
    return wikitext[start_match.end() : end]


def parse_weapon_reference(wikitext: str) -> list[dict[str, Any]]:
    block = section(wikitext, "Weapons", "Weapon Passives")
    weapons: list[dict[str, Any]] = []
    for row in re.split(r"(?m)^\|-\s*$", block)[1:]:
        cells = table_cells(row)
        if len(cells) < 7 or not re.fullmatch(r"\d{3}", clean_wiki_text(cells[0])):
            continue
        weapon_id = int(clean_wiki_text(cells[0]))
        weapons.append(
            {
                "id": weapon_id,
                "name": clean_wiki_text(cells[2]),
                "weapon_stats": parse_int_for_catalog(clean_wiki_text(cells[3])),
                "passive_slots": parse_int_for_catalog(clean_wiki_text(cells[4])),
                "wp_range": clean_wiki_text(cells[5]),
                "description": clean_wiki_text(" ".join(cells[6:])),
            }
        )
    return weapons


def parse_int_for_catalog(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def parse_passive_reference(wikitext: str) -> list[dict[str, str]]:
    block = section(wikitext, "Weapon Passives", "Limited Time Weapons")
    passives: list[dict[str, str]] = []
    for row in re.split(r"(?m)^\|-\s*$", block)[1:]:
        cells = table_cells(row)
        cleaned = [clean_wiki_text(cell) for cell in cells]
        cleaned = [cell for cell in cleaned if cell]
        if len(cleaned) < 2:
            continue
        name = cleaned[-2]
        description = cleaned[-1]
        if name.casefold() in {"name", "passive"} or len(name) > 80:
            continue
        passives.append({"name": name, "description": description})
    unique: dict[str, dict[str, str]] = {}
    for passive in passives:
        unique[passive["name"].casefold()] = passive
    return list(unique.values())


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def mediawiki_file_key(value: str) -> str:
    return urllib.parse.unquote(value).replace("_", " ").casefold().strip()


def image_urls(filenames: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for batch in chunks(filenames, 40):
        requested = {mediawiki_file_key(name): name for name in batch}
        titles = "|".join(f"File:{name}" for name in batch)
        payload = api(
            {
                "action": "query",
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": "128",
                "titles": titles,
            }
        )
        for page in payload.get("query", {}).get("pages", []):
            title = str(page.get("title", ""))
            returned_filename = title.removeprefix("File:")
            filename = requested.get(mediawiki_file_key(returned_filename), returned_filename)
            info = (page.get("imageinfo") or [{}])[0]
            url = str(info.get("thumburl") or info.get("url") or "")
            if filename and url:
                found[filename] = url
    return found


def extension_for(filename: str, url: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.casefold()
    if suffix not in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        suffix = Path(filename).suffix.casefold()
    return suffix if suffix in {".gif", ".jpeg", ".jpg", ".png", ".webp"} else ".webp"


def write_asset(directory: Path, stem: str, filename: str, url: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = extension_for(filename, url)
    path = directory / f"{stem}{suffix}"
    if path.is_file() and path.stat().st_size > 0:
        return path.relative_to(PROJECT_ROOT).as_posix()
    path.write_bytes(fetch_bytes(url))
    return path.relative_to(PROJECT_ROOT).as_posix()


def main() -> None:
    special_wikitext = fetch_wikitext(SPECIAL_TEMPLATE)
    weapons_wikitext = fetch_wikitext("Weapons")
    animals = parse_special_animals(special_wikitext)
    weapons = parse_weapon_reference(weapons_wikitext)
    passives = parse_passive_reference(weapons_wikitext)
    if not 150 <= len(animals) <= 500:
        raise RuntimeError(f"Unexpected Special-animal count: {len(animals)}")
    if len(weapons) != 29:
        raise RuntimeError(f"Unexpected weapon count: {len(weapons)}")
    if len(passives) != 28:
        raise RuntimeError(f"Unexpected passive count: {len(passives)}")

    filenames = [str(animal["source_image"]) for animal in animals]
    urls = image_urls([*filenames, *RANK_FILES.values()])
    missing = sorted(set([*filenames, *RANK_FILES.values()]) - set(urls))
    if missing:
        raise RuntimeError(f"Wiki image URLs missing for: {', '.join(missing)}")

    for animal in animals:
        filename = str(animal["source_image"])
        animal["asset"] = write_asset(
            ANIMAL_ASSET_DIR,
            str(animal["emoji_stem"]),
            filename,
            urls[filename],
        )

    rank_assets: dict[str, str] = {}
    for rank, filename in RANK_FILES.items():
        rank_assets[rank] = write_asset(RANK_ASSET_DIR, rank, filename, urls[filename])

    payload = {
        "schema_version": 1,
        "source": SOURCE_PAGE,
        "source_template": SPECIAL_TEMPLATE,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "special_animal_count": len(animals),
        "animals": animals,
        "rank_assets": rank_assets,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = DATA_DIR / "special_animals.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    reference_output = DATA_DIR / "game_reference.json"
    reference_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": WEAPONS_PAGE,
                "retrieved_at": payload["retrieved_at"],
                "weapons": weapons,
                "passives": passives,
                "weapon_quality_tiers": [
                    {"rank": "common", "quality": "0-20%"},
                    {"rank": "uncommon", "quality": "21-40%"},
                    {"rank": "rare", "quality": "41-60%"},
                    {"rank": "epic", "quality": "61-80%"},
                    {"rank": "mythical", "quality": "81-94%"},
                    {"rank": "legendary", "quality": "95-99%"},
                    {"rank": "fabled", "quality": "100%"},
                ],
                "animal_ranks": list(RANK_FILES),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(animals)} Special animals to {output}")
    print(f"Wrote {len(rank_assets)} rank assets to {RANK_ASSET_DIR}")
    print(f"Wrote {len(weapons)} weapons and {len(passives)} passives to {reference_output}")


if __name__ == "__main__":
    main()
