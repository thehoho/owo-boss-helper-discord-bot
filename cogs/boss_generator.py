"""
cogs/boss_generator.py — automatic OwO boss command generator and cooldown tracker.

Generator:
- Watches only exact OwO boss-inventory commands after whitespace is removed:
  `owobossi` and `wbossi` (so `owo boss i`, `owoboss i`, `w boss i`, etc. work).
- Reads the three paginated OwO boss cards, orders them by the visible 1/3–3/3 counter, and posts the Neon battle command.

Cooldown tracker:
- Uses gateway payloads to discover status cards only while a boss is active or can spawn.
- Starts a 5-minute cooldown only when a guild boss is defeated.
- Marks the guild ready immediately when a boss escapes; escapes have no cooldown.
- Announces newly detected guild bosses and supports `H help`, `H boss cd`, and `H boss cooldown`.
- Writes runtime activity to the rotating log configured by bot.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# CONSTANTS & CONFIG
# ──────────────────────────────────────────────────────────────

DEFAULT_HP = "80000"
LVL_RE = re.compile(r"Lvl\s+\d+", re.I)
PAGE_POSITION_RE = re.compile(r"^\s*([1-3])\s*/\s*3\s*$")
PAGE_POSITION_SEARCH_RE = re.compile(r"(?<!\d)([1-3])\s*/\s*3(?!\d)")

# Official verified OwO Bot user ID.
OWO_BOT_ID = 408785106942164992

# Only these two commands are accepted after all whitespace is removed.
ALLOWED_BOSS_TRIGGERS = {"owobossi", "wbossi"}

# Lightweight public helper commands. Whitespace and capitalization are ignored.
PREFIX_COOLDOWN_TRIGGERS = {"hbosscd", "hbosscooldown"}
PREFIX_HELP_TRIGGERS = {"hhelp"}

SESSION_TIMEOUT_SECONDS = 180
BOSS_COOLDOWN_SECONDS = 5 * 60
OUTCOME_DEDUP_SECONDS = 20
OUTCOME_SETTLE_SECONDS = 1.25
BOSS_WATCH_INTERVAL_SECONDS = 15

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOLDOWN_CONFIG_FILE = PROJECT_ROOT / "boss_cooldown_config.json"
HP_TEMPLATE_DIR = PROJECT_ROOT / "assets" / "hp_digits"


def is_boss_trigger(content: str) -> bool:
    """Accept only `owo boss i` or `w boss i`, with any whitespace/capitalization."""
    normalized = re.sub(r"\s+", "", content or "").lower()
    return normalized in ALLOWED_BOSS_TRIGGERS


def is_prefix_cooldown_trigger(content: str) -> bool:
    """Accept `H boss cd` and `H boss cooldown`, case-insensitively."""
    normalized = re.sub(r"\s+", "", content or "").lower()
    return normalized in PREFIX_COOLDOWN_TRIGGERS


def is_prefix_help_trigger(content: str) -> bool:
    """Accept `H help`, case-insensitively and with flexible whitespace."""
    normalized = re.sub(r"\s+", "", content or "").lower()
    return normalized in PREFIX_HELP_TRIGGERS


def load_cooldown_config() -> dict[str, dict[str, Any]]:
    if not COOLDOWN_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(COOLDOWN_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cooldown_config(data: dict[str, dict[str, Any]]) -> None:
    """Write config atomically so an interrupted write does not corrupt it."""
    temp_file = COOLDOWN_CONFIG_FILE.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_file.replace(COOLDOWN_CONFIG_FILE)


# These intentionally use past-tense result wording to avoid matching instructions
# such as "defeat the boss".
DEFEATED_PATTERNS = (
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,160}\b(?:has\s+been\s+|was\s+)?(?:defeated|slain|killed)\b", re.I),
    re.compile(r"\b(?:defeated|slain|killed)\b.{0,160}\b(?:the\s+|guild\s+)?boss\b", re.I),
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,160}\bhas\s+fallen\b", re.I),
)

ESCAPED_PATTERNS = (
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,160}\b(?:has\s+|was\s+)?(?:escaped|fled|ran\s+away)\b", re.I),
    re.compile(r"\b(?:escaped|fled|ran\s+away)\b.{0,160}\b(?:the\s+|guild\s+)?boss\b", re.I),
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,160}\bgot\s+away\b", re.I),
)


def detect_boss_outcome(text: str) -> str | None:
    """Return `defeated`, `escaped`, or None from an OwO message's full text."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return None
    if any(pattern.search(normalized) for pattern in DEFEATED_PATTERNS):
        return "defeated"
    if any(pattern.search(normalized) for pattern in ESCAPED_PATTERNS):
        return "escaped"
    return None


WEAPON_MAP = {
    "great sword":"sword","greatsword":"sword","sword":"sword",
    "healing staff":"hstaff","heal staff":"hstaff","hstaff":"hstaff",
    "bow":"bow",
    "rune of the forgotten":"rune","forgotten rune":"rune","rune":"rune",
    "defender s aegis":"shield","defenders aegis":"shield","aegis":"shield","shield":"shield",
    "orb of potency":"orb","potency orb":"orb","orb":"orb",
    "vampiric staff":"vstaff","vamp staff":"vstaff","vstaff":"vstaff",
    "poison dagger":"pd","dagger":"pd","pd":"pd",
    "wand of absorption":"wand","absorption wand":"wand","wand":"wand",
    "flame staff":"fstaff","fire staff":"fstaff","fstaff":"fstaff",
    "energy staff":"estaff","estaff":"estaff",
    "spirit staff":"sstaff","sstaff":"sstaff",
    "arcane scepter":"ascept","scepter":"ascept","ascept":"ascept",
    "resurrection staff":"rstaff","res staff":"rstaff","rstaff":"rstaff",
    "glacial axe":"axe","axe":"axe",
    "vanguard s banner":"vban","banner":"vban","vban":"vban",
    "culling scythe":"sythe","scythe":"sythe","sythe":"sythe",
    "rune of celebration":"crune","celebration rune":"crune","crune":"crune",
    "staff of purity":"pstaff","purity staff":"pstaff","pstaff":"pstaff",
    "leeching scythe":"lsy","leech scythe":"lsy","lsy":"lsy",
    "foul fish":"ffish","fishing rod":"ffish","fish":"ffish","ffish":"ffish",
    "rune of luck":"lrune","luck rune":"lrune","lrune":"lrune",
    "staff of corruption":"cstaff","corruption staff":"cstaff","cstaff":"cstaff",
    "soul tithe":"soul","soul":"soul",
    "briar heart staff":"bhstaff","briar-heart staff":"bhstaff","bhstaff":"bhstaff",
    "arbiter s edge":"aedge","arbiter edge":"aedge","edge":"aedge","aedge":"aedge",
    "wounding crossbow":"xbow","crossbow":"xbow","xbow":"xbow",
    "bleeding gaze":"bgaz","gaze":"bgaz","bgaz":"bgaz",
    "conduit claw":"cclaw","claw":"cclaw","cclaw":"cclaw",
}

PASSIVE_MAP = {
    "strength":"str","str":"str",
    "magic":"mag","mag":"mag",
    "health point":"hp","health":"hp","hp":"hp",
    "weapon point":"wp","wp":"wp",
    "physical resistance":"pr","pr":"pr",
    "magic resistance":"mr","magical resistance":"mr","mr":"mr",
    "lifesteal":"ls","ls":"ls",
    "thorns":"th","th":"th",
    "mana tap":"mtap","mtap":"mtap",
    "absolve":"absv","absv":"absv",
    "safeguard":"sg","sg":"sg",
    "critical":"crit","crit":"crit",
    "discharge":"dc","dc":"dc",
    "kamikaze":"kk","kk":"kk",
    "regeneration":"hgen","regen":"hgen","hgen":"hgen",
    "energize":"wgen","wgen":"wgen",
    "sprout":"sprout","sprt":"sprout",
    "enrage":"enrage","enra":"enrage",
    "sacrifice":"sac","sac":"sac",
    "snail":"snail",
    "knowledge":"kno","kno":"kno",
    "giant slayer":"gslay","gslay":"gslay",
    "adaptation":"adapt","adapt":"adapt",
    "resonance":"res","reso":"res","res":"res",
    "living hive":"swarm","swarm":"swarm",
    "lone wolf":"lwolf","lwolf":"lwolf",
    "double strike":"ds","ds":"ds",
    "frost armor":"fr","fr":"fr",
}

RARITIES_SET = {
    "common","uncommon","rare","epic","mythical","legendary",
    "fabled","hidden","special","patreon","gem","bot","distorted",
}
REMOVE_WORDS = {
    "pristine","fine","decent","worn","unknown","empowered","unempowered",
    "used","new","old","broken","damaged","poor","good","excellent","perfect",
}
EXACT_WEAR_MULTIPLIER = {
    "WORN":1,"DECENT":1.01,"FINE":1.03,"PRISTINE":1.05,"UNKNOWN":1,
}
EXACT_MODIFIER_WORDS = {
    "Worn","Decent","Fine","Pristine","Unknown",
    "Common","Uncommon","Rare","Epic","Legendary","Mythic","Mythical",
    "Divine","Fabled","Empowered","Shiny","Boss",
}
EXACT_WEAPONS = {
    "Great Sword":          {"values":[[35,55],[200,100]],                             "alias":"sword"},
    "Healing Staff":        {"values":[[110,160],[225,150]],                           "alias":"hstaff"},
    "Bow":                  {"values":[[110,160],[220,120]],                           "alias":"bow"},
    "Rune of the Forgotten":{"values":[[5,15]],                                        "alias":"rune"},
    "Defender's Aegis":     {"values":[[30,50],[250,150]],                             "alias":"shield"},
    "Orb of Potency":       {"values":[],                                              "alias":"orb"},
    "Vampiric Staff":       {"values":[[25,45],[190,90]],                              "alias":"vstaff"},
    "Poison Dagger":        {"values":[[70,100],[30,50],[200,100]],                    "alias":"pd"},
    "Wand of Absorption":   {"values":[[80,115],[20,40],[250,150]],                    "alias":"wand"},
    "Flame Staff":          {"values":[[75,95],[20,40],[70,100],[200,100]],             "alias":"fstaff"},
    "Energy Staff":         {"values":[[35,65],[200,100]],                             "alias":"estaff"},
    "Spirit Staff":         {"values":[[30,50],[20,30],[250,150]],                     "alias":"sstaff"},
    "Arcane Scepter":       {"values":[[65,95],[200,125]],                             "alias":"ascept"},
    "Resurrection Staff":   {"values":[[60,90],[400,300]],                             "alias":"rstaff"},
    "Glacial Axe":          {"values":[[40,60],[260,160]],                             "alias":"axe"},
    "Vanguard's Banner":    {"values":[[15,25],[25,35],[40,50],[290,235]],             "alias":"vban"},
    "Culling Scythe":       {"values":[[70,100],[45,75],[200,100]],                    "alias":"sythe"},
    "Rune of Celebration":  {"values":[[20,45],[15,35],[200,100]],                     "alias":"crune"},
    "Staff of Purity":      {"values":[[50,100],[15,25],[250,150]],                    "alias":"pstaff"},
    "Leeching Scythe":      {"values":[[50,80],[40,60],[30,60],[30,60],[230,130]],     "alias":"lsy"},
    "Foul Fish":            {"values":[[50,80],[20,50],[280,180]],                     "alias":"ffish"},
    "Rune of Luck":         {"values":[[1,40],[1,40],[1,40],[1,40],[1,40],[200,100]], "alias":"lrune"},
    "Staff of Corruption":  {"values":[[70,50],[80,120],[250,150]],                    "alias":"cstaff"},
    "Soul Tithe":           {"values":[[10,25],[0.35,0.45],[100,50]],                  "alias":"soul"},
    "Briar-Heart Staff":    {"values":[[25,50],[20,30],[20,30],[240,140]],             "alias":"bhstaff"},
    "Arbiter's Edge":       {"values":[[10,20],[20,30],[225,125]],                     "alias":"aedge"},
    "Wounding Crossbow":    {"values":[[220,300],[10,25],[480,280]],                   "alias":"xbow"},
    "Bleeding Gaze":        {"values":[[20,10],[30,50],[20,40],[150,200]],             "alias":"bgaz"},
    "Conduit Claw":         {"values":[[20,50],[120,170],[200,100]],                   "alias":"cclaw"},
}
EXACT_PASSIVES = {
    "Strength":           {"values":[[5,20]],          "alias":"str"},
    "Magic":              {"values":[[5,20]],          "alias":"mag"},
    "Health Point":       {"values":[[5,20]],          "alias":"hp"},
    "Weapon Point":       {"values":[[10,30]],         "alias":"wp"},
    "Physical Resistance":{"values":[[15,35]],         "alias":"pr"},
    "Magical Resistance": {"values":[[15,35]],         "alias":"mr"},
    "Magic Resistance":   {"values":[[15,35]],         "alias":"mr"},
    "Lifesteal":          {"values":[[15,35]],         "alias":"ls"},
    "Thorns":             {"values":[[15,35]],         "alias":"th"},
    "Mana Tap":           {"values":[[15,30]],         "alias":"mtap"},
    "Absolve":            {"values":[[60,80]],         "alias":"absv"},
    "Safeguard":          {"values":[[20,40]],         "alias":"sg"},
    "Critical":           {"values":[[10,30],[25,50]], "alias":"crit"},
    "Discharge":          {"values":[[110,150]],       "alias":"dc"},
    "Kamikaze":           {"values":[[50,75]],         "alias":"kk"},
    "Regeneration":       {"values":[[5,10]],          "alias":"hgen"},
    "Energize":           {"values":[[20,40]],         "alias":"wgen"},
    "Sprout":             {"values":[[20,40]],         "alias":"sprout"},
    "Enrage":             {"values":[[2,5]],           "alias":"enrage"},
    "Snail":              {"values":[[5,15]],          "alias":"snail"},
    "Sacrifice":          {"values":[[25,50],[15,35]], "alias":"sac"},
    "Knowledge":          {"values":[[5,15]],          "alias":"kno"},
    "Giant Slayer":       {"values":[[10,25]],         "alias":"gslay"},
    "Adaptation":         {"values":[[5,10],[5,10]],   "alias":"adapt"},
    "Resonance":          {"values":[[5,10],[5,10]],   "alias":"res"},
    "Living Hive":        {"values":[[8,2],[2,8]],     "alias":"swarm"},
    "Lone Wolf":          {"values":[[10,30],[10,30]], "alias":"lwolf"},
    "Double Strike":      {"values":[[10,25],[15,30],[35,20]],"alias":"ds"},
    "Frost Armor":        {"values":[[10,20]],         "alias":"fr"},
}


# ──────────────────────────────────────────────────────────────
#  LOW-LEVEL HELPERS
# ──────────────────────────────────────────────────────────────

def normalize_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"<:[^>]+>"," ",value)
    value = value.replace("\u2019","'").replace("`","")
    value = re.sub(r"['\u2018]"," ",value)
    value = re.sub(r"[^a-z0-9\s-]"," ",value)
    value = value.replace("-"," ")
    return re.sub(r"\s+"," ",value).strip()


def normalize_hp(value: str) -> str:
    value = value.strip().lower().replace(",","")
    if not value: return DEFAULT_HP
    if value.endswith("k"):
        try: return str(int(float(value[:-1])*1000))
        except ValueError: return DEFAULT_HP
    try: int(value); return value
    except ValueError: return DEFAULT_HP


def clean_weapon_name(raw: str) -> tuple:
    cleaned = normalize_name(raw)
    for pat in [r"\bquality\b.*",r"\bwear\b.*",r"\btype\b.*",
                r"\bkills\b.*",r"\bweapon cost\b.*"]:
        cleaned = re.sub(pat,"",cleaned).strip()
    words = [w for w in cleaned.split() if w not in RARITIES_SET and w not in REMOVE_WORDS]
    name  = " ".join(words).strip()
    if name in WEAPON_MAP: return WEAPON_MAP[name], None
    fallback = name.replace(" ","")
    return fallback, f"Unknown weapon: '{raw.strip()}' → fallback: '{fallback}'"


_EXACT_WEAPON_BY_NORM  = {normalize_name(k):k for k in EXACT_WEAPONS}
_EXACT_PASSIVE_BY_NORM = {normalize_name(k):k for k in EXACT_PASSIVES}



# ──────────────────────────────────────────────────────────────
# MESSAGE / COMPONENTS-V2 EXTRACTION
# ──────────────────────────────────────────────────────────────


def _build_parse_text(boss_title: str, description: str) -> str:
    if re.match(r"##\s*Lvl\s*\d+", (description or "").strip(), re.I):
        return description
    return f"## {boss_title}\n{description}"


def extract_text_from_components(components: list) -> str:
    """Recursively collect text from Discord components-v2 payloads."""
    chunks: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            content = node.get("content")
            if isinstance(content, str) and content:
                chunks.append(content)
            for key in ("components", "accessory"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(components)
    return "\n".join(chunks)


def _find_boss_title_from_content(content: str) -> str:
    for line in (content or "").split("\n")[:8]:
        clean = re.sub(r"[#*_`]", "", line).strip()
        if LVL_RE.search(clean):
            return clean
    return ""


def _find_boss_title_dict(embed: dict[str, Any]) -> str:
    title = str(embed.get("title") or "").strip()
    if LVL_RE.search(title):
        return title

    author = embed.get("author") or {}
    author_name = str(author.get("name") or "").strip()
    if LVL_RE.search(author_name):
        return author_name

    description = str(embed.get("description") or "")
    return _find_boss_title_from_content(description)


async def fetch_raw_message(bot: commands.Bot, channel_id: int, message_id: int) -> dict[str, Any] | None:
    """Fetch raw message JSON so components-v2 text is available."""
    try:
        route = discord.http.Route(
            "GET",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )
        return await bot.http.request(route)
    except discord.NotFound:
        logger.info("Raw message %s no longer exists", message_id)
        return None
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not read raw message %s: %s", message_id, exc)
        return None


def message_to_raw_data(message: discord.Message) -> dict[str, Any]:
    """Build raw-like data from the gateway Message without another API request.

    Discord already sends message content, embeds, Components V2, and attachments
    over the gateway. Using that payload for discovery prevents one REST GET for
    every OwO response in busy grinding channels.
    """
    components: list[dict[str, Any]] = []
    for component in getattr(message, "components", []) or []:
        to_dict = getattr(component, "to_dict", None)
        if callable(to_dict):
            try:
                components.append(to_dict())
            except Exception:
                continue

    attachments: list[dict[str, Any]] = []
    for attachment in getattr(message, "attachments", []) or []:
        attachments.append(
            {
                "id": str(getattr(attachment, "id", "")),
                "filename": getattr(attachment, "filename", ""),
                "url": getattr(attachment, "url", ""),
                "proxy_url": getattr(attachment, "proxy_url", ""),
                "content_type": getattr(attachment, "content_type", None),
            }
        )

    created_at = getattr(message, "created_at", None)
    edited_at = getattr(message, "edited_at", None)
    return {
        "id": str(message.id),
        "channel_id": str(message.channel.id),
        "guild_id": str(message.guild.id) if message.guild else None,
        "author": {"id": str(message.author.id)},
        "content": message.content or "",
        "embeds": [embed.to_dict() for embed in message.embeds],
        "components": components,
        "attachments": attachments,
        "timestamp": created_at.isoformat() if created_at else None,
        "edited_timestamp": edited_at.isoformat() if edited_at else None,
    }


def extract_all_text_from_raw(data: dict[str, Any]) -> str:
    """Combine content, embed text, fields, and components-v2 text."""
    chunks: list[str] = []

    content = data.get("content")
    if isinstance(content, str) and content:
        chunks.append(content)

    for embed in data.get("embeds", []):
        for value in (
            embed.get("title"),
            embed.get("description"),
            (embed.get("author") or {}).get("name"),
            (embed.get("footer") or {}).get("text"),
        ):
            if isinstance(value, str) and value:
                chunks.append(value)
        for field in embed.get("fields", []):
            name = field.get("name")
            value = field.get("value")
            if isinstance(name, str) and name:
                chunks.append(name)
            if isinstance(value, str) and value:
                chunks.append(value)

    component_text = extract_text_from_components(data.get("components", []))
    if component_text:
        chunks.append(component_text)

    return "\n".join(chunks)


def extract_boss_page_number(data: dict[str, Any]) -> int | None:
    """Read OwO's visible `1/3`, `2/3`, or `3/3` page indicator.

    OwO normally stores the page counter as a button label inside the message's
    component tree. The recursive walk also supports nested components-v2 layouts.
    A limited text fallback is included in case OwO moves the counter into message
    content or an embed footer later.
    """
    component_matches: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # Button labels are the authoritative source. Check other short string
            # values too because Discord component layouts can change over time.
            for key, value in node.items():
                if isinstance(value, str):
                    match = PAGE_POSITION_RE.fullmatch(value)
                    if match:
                        component_matches.append(int(match.group(1)))
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data.get("components", []))
    if component_matches:
        return component_matches[0]

    # Conservative fallback outside components.
    fallback_chunks: list[str] = []
    content = data.get("content")
    if isinstance(content, str):
        fallback_chunks.append(content)

    for embed in data.get("embeds", []):
        for value in (
            embed.get("title"),
            embed.get("description"),
            (embed.get("footer") or {}).get("text"),
        ):
            if isinstance(value, str):
                fallback_chunks.append(value)

    for chunk in fallback_chunks:
        match = PAGE_POSITION_SEARCH_RE.search(chunk)
        if match:
            return int(match.group(1))

    return None



def extract_media_urls(data: dict[str, Any]) -> list[str]:
    """Collect image/media URLs from embeds and Components V2 payloads."""
    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            if value not in urls:
                urls.append(value)

    for embed in data.get("embeds", []):
        for key in ("image", "thumbnail"):
            obj = embed.get(key) or {}
            add(obj.get("url"))
            add(obj.get("proxy_url"))

    for attachment in data.get("attachments", []):
        if isinstance(attachment, dict):
            add(attachment.get("url"))
            add(attachment.get("proxy_url"))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            media = node.get("media")
            if isinstance(media, dict):
                add(media.get("url"))
                add(media.get("proxy_url"))
            attachment = node.get("attachment")
            if isinstance(attachment, dict):
                add(attachment.get("url"))
                add(attachment.get("proxy_url"))
            # Some payloads expose the URL directly on a media-gallery item.
            if node.get("type") in (11, 12, 13):
                add(node.get("url"))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data.get("components", []))
    return urls


def _normalize_glyph(mask: list[list[bool]]) -> Image.Image | None:
    """Trim and center one binary glyph on the same canvas as our templates."""
    if not mask or not mask[0]:
        return None
    h, w = len(mask), len(mask[0])
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if mask[y][x]:
                xs.append(x); ys.append(y)
    if not xs:
        return None
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    glyph = Image.new("L", (right-left+1, bottom-top+1), 0)
    px = glyph.load()
    for y in range(top, bottom+1):
        for x in range(left, right+1):
            if mask[y][x]:
                px[x-left, y-top] = 255
    # Preserve pixel-art edges. Scale down only if an unexpected larger image appears.
    if glyph.width > 14 or glyph.height > 18:
        ratio = min(14/glyph.width, 18/glyph.height)
        glyph = glyph.resize((max(1, round(glyph.width*ratio)), max(1, round(glyph.height*ratio))), Image.Resampling.NEAREST)
    canvas = Image.new("L", (16, 20), 0)
    canvas.paste(glyph, ((16-glyph.width)//2, (20-glyph.height)//2))
    return canvas


def load_hp_templates() -> dict[str, list[Image.Image]]:
    templates: dict[str, list[Image.Image]] = {}
    if not HP_TEMPLATE_DIR.exists():
        return templates
    for path in HP_TEMPLATE_DIR.glob("*.png"):
        name = path.stem.split("_", 1)[0]
        char = "/" if name == "slash" else "," if name == "comma" else name
        try:
            templates.setdefault(char, []).append(Image.open(path).convert("L"))
        except OSError:
            continue
    return templates


def _glyph_similarity(a: Image.Image, b: Image.Image) -> float:
    ap = list(a.getdata()); bp = list(b.getdata())
    both = union = 0
    for av, bv in zip(ap, bp):
        aa, bb = av > 127, bv > 127
        both += int(aa and bb)
        union += int(aa or bb)
    return both / union if union else 0.0


def _best_glyph_match(
    glyph: Image.Image,
    templates: dict[str, list[Image.Image]],
    allowed_chars: set[str] | None = None,
) -> tuple[str | None, float]:
    """Return the best matching template character and its similarity score."""
    best_char: str | None = None
    best_score = -1.0
    for char, options in templates.items():
        if allowed_chars is not None and char not in allowed_chars:
            continue
        score = max(_glyph_similarity(glyph, template) for template in options)
        if score > best_score:
            best_char, best_score = char, score
    return best_char, best_score


def _recognize_hp_run(
    glyph_mask: list[list[bool]],
    templates: dict[str, list[Image.Image]],
) -> list[tuple[str, float]]:
    """Recognize one projected run, splitting two touching digits when needed.

    OwO's pixel font occasionally renders adjacent digits without a completely blank
    column between them. A known example is ``74``, which appears as one 15-pixel
    run. Treating that run as a single glyph can look vaguely like ``0``. For wide
    runs, try every sensible two-digit split and use it only when both resulting
    characters are strong digit matches.
    """
    glyph = _normalize_glyph(glyph_mask)
    if glyph is None:
        return []

    single_char, single_score = _best_glyph_match(glyph, templates)
    width = len(glyph_mask[0]) if glyph_mask else 0

    # Normal OwO digits are roughly 3-8 pixels wide. Runs wider than 10 pixels
    # are usually two touching digits rather than one legitimate character.
    if width > 10:
        digit_chars = set("0123456789")
        best_pair: list[tuple[str, float]] | None = None
        best_pair_score = -1.0

        for split_at in range(3, width - 2):
            left_mask = [row[:split_at] for row in glyph_mask]
            right_mask = [row[split_at:] for row in glyph_mask]
            left_glyph = _normalize_glyph(left_mask)
            right_glyph = _normalize_glyph(right_mask)
            if left_glyph is None or right_glyph is None:
                continue

            left_char, left_score = _best_glyph_match(
                left_glyph, templates, digit_chars
            )
            right_char, right_score = _best_glyph_match(
                right_glyph, templates, digit_chars
            )
            if left_char is None or right_char is None:
                continue

            pair_score = (left_score + right_score) / 2
            if pair_score > best_pair_score:
                best_pair_score = pair_score
                best_pair = [(left_char, left_score), (right_char, right_score)]

        # Require both halves to be convincing and materially better than treating
        # the entire wide run as one character.
        if (
            best_pair
            and min(score for _, score in best_pair) >= 0.60
            and best_pair_score >= single_score + 0.15
        ):
            return best_pair

    return [(single_char, single_score)] if single_char is not None else []


def read_hp_from_image_bytes(image_bytes: bytes, templates: dict[str, list[Image.Image]]) -> tuple[str | None, float]:
    """Read the left/current HP from OwO's fixed 600x140 boss image."""
    if not templates:
        return None, 0.0
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except OSError:
        return None, 0.0

    # OwO's HP text lives in the lower bar. Scale coordinates for equivalent sizes.
    width, height = image.size
    if width < 250 or height < 70:
        return None, 0.0
    y1, y2 = round(height * 118/140), round(height * 136/140)
    x1, x2 = round(width * 0.25), round(width * 0.75)
    crop = image.crop((x1, y1, x2, y2))
    pixels = crop.load()
    mask = [[False] * crop.width for _ in range(crop.height)]
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = pixels[x, y]
            mask[y][x] = r > 175 and g > 175 and b > 175 and max(r, g, b) - min(r, g, b) < 45

    projection = [sum(mask[y][x] for y in range(crop.height)) for x in range(crop.width)]
    runs: list[tuple[int, int]] = []
    start = None
    for x, count in enumerate(projection):
        if count and start is None:
            start = x
        if start is not None and (count == 0 or x == crop.width - 1):
            end = x - 1 if count == 0 else x
            runs.append((start, end)); start = None

    chars: list[str] = []
    scores: list[float] = []
    for left, right in runs:
        glyph_mask = [row[left:right+1] for row in mask]
        for char, score in _recognize_hp_run(glyph_mask, templates):
            chars.append(char)
            scores.append(score)

    text = "".join(chars)
    match = re.fullmatch(r"([0-9][0-9,]*)/([0-9][0-9,]*)", text)
    confidence = sum(scores) / len(scores) if scores else 0.0
    minimum_score = min(scores) if scores else 0.0

    # A high average must not hide one severely misread glyph.
    if not match or confidence < 0.65 or minimum_score < 0.50:
        return None, confidence

    current = match.group(1).replace(",", "")
    maximum = match.group(2).replace(",", "")

    # HP values never contain unnecessary leading zeroes. Rejecting them prevents
    # a weak merged-glyph guess such as ``0589`` from entering the final command.
    if (current.startswith("0") and current != "0") or maximum.startswith("0"):
        return None, confidence

    try:
        if int(current) < 0 or int(maximum) <= 0 or int(current) > int(maximum):
            return None, confidence
    except ValueError:
        return None, confidence
    return current, confidence


def is_guild_boss_status(data: dict[str, Any]) -> bool:
    """Recognize OwO's server-wide guild-boss status card, not inventory pages."""
    text = re.sub(r"\s+", " ", extract_all_text_from_raw(data)).lower()
    if "lvl " in text:
        return False
    return ("fighters" in text and "defeated" in text) or ("guild boss" in text and "fight" in text) or detect_boss_outcome(text) is not None


def extract_discord_timestamps(data: dict[str, Any]) -> list[int]:
    """Return every Discord timestamp embedded in an OwO status message."""
    text = extract_all_text_from_raw(data)
    return [int(value) for value in re.findall(r"<t:(\d{9,12})(?::[A-Za-z])?>", text)]


def extract_future_boss_expiry(data: dict[str, Any], now: int | None = None) -> int | None:
    """Return the active boss's future escape timestamp when OwO exposes one."""
    now = now or int(time.time())
    future = [value for value in extract_discord_timestamps(data) if value > now]
    # The status card normally contains one future expiry. If it ever contains
    # more than one, the latest future timestamp is the safest expiry candidate.
    return max(future) if future else None


def extract_relevant_timestamp(data: dict[str, Any], now: int | None = None) -> int | None:
    """Extract OwO's real relative Discord timestamp, falling back to edit/create time."""
    now = now or int(time.time())
    candidates = extract_discord_timestamps(data)
    past = [value for value in candidates if value <= now + 60]
    if past:
        return max(past)
    for key in ("edited_timestamp", "timestamp"):
        value = data.get(key)
        if isinstance(value, str) and value:
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    return None

def extract_boss_from_raw(data: dict[str, Any]) -> tuple[str, str]:
    """Return (boss title, parser text) from content, embeds, or components-v2."""
    content = str(data.get("content") or "")
    title = _find_boss_title_from_content(content)
    if title:
        return title, content

    for embed in data.get("embeds", []):
        title = _find_boss_title_dict(embed)
        if title:
            description = str(embed.get("description") or "")
            return title, _build_parse_text(title, description)

    component_text = extract_text_from_components(data.get("components", []))
    title = _find_boss_title_from_content(component_text)
    if title:
        return title, component_text

    return "", ""


def extract_hp_from_embed(description: str) -> str:
    """Try to read the HP pool from 'X,XXX / X,XXX' text. Falls back to DEFAULT_HP."""
    m = re.search(r"(\d[\d,]+)\s*/\s*(\d[\d,]+)", description)
    if m:
        max_hp = m.group(2).replace(",","")
        try:
            if int(max_hp) > 0: return max_hp
        except ValueError:
            pass
    return DEFAULT_HP


def split_boss_blocks(text: str) -> list:
    text    = text.strip()
    matches = list(re.finditer(r"##\s*Lvl\s*\d+", text, flags=re.I))
    if not matches: return []
    blocks  = []
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        blocks.append(text[m.start():end].strip())
    return blocks


# ──────────────────────────────────────────────────────────────
#  EXACT PARSER
# ──────────────────────────────────────────────────────────────

def _exact_number_values(section: str) -> list:
    vals, seen = [], []
    section = re.sub(r"<:[^>]+>"," ",section)
    for pat in [
        r"[-+]?\s*\*\*\s*[-+]?\s*(\d+(?:\.\d+)?)\s*%?\s*\*\*",
        r"\*\*\s*[-+]?\s*(\d+(?:\.\d+)?)\s*%?\s*\*\*",
    ]:
        for m in re.finditer(pat, section):
            sp = m.span()
            if any(not(sp[1]<=s[0] or sp[0]>=s[1]) for s in seen): continue
            vals.append(float(m.group(1))); seen.append(sp)
    return vals


def _extract_exact_weapon_type(header: str) -> str:
    words = header.strip().split()
    while words and words[0] in EXACT_MODIFIER_WORDS: words.pop(0)
    candidate = " ".join(words).strip()
    key = _EXACT_WEAPON_BY_NORM.get(normalize_name(candidate))
    if key: return key
    words2 = [w for w in normalize_name(candidate).split()
              if w not in RARITIES_SET and w not in {x.lower() for x in EXACT_MODIFIER_WORDS}]
    key = _EXACT_WEAPON_BY_NORM.get(" ".join(words2))
    if key: return key
    raise ValueError(f"Unknown exact weapon: {candidate or header}")


def _extract_exact_animal(block: str) -> str:
    compact = " ".join(block.split())
    m = re.search(r"##\s*Lvl\s*(\d+)\s+(.+?)(?=<:|###|-#|\*\*|$)", compact, re.I)
    if not m: raise ValueError("Could not find exact boss level/name.")
    level       = m.group(1)
    title_words = m.group(2).strip().split()
    while title_words and normalize_name(title_words[0]) in RARITIES_SET:
        title_words.pop(0)
    animal = "_".join(normalize_name(" ".join(title_words)).split()) or "unknown"
    return f"{level} {animal}"


def _exact_rarity_from_raw(value:float,low:float,high:float,wear:str)->int:
    raw = 100.0*((value-low)/(high-low)-EXACT_WEAR_MULTIPLIER[wear]+1)
    return max(1,min(100,round(raw)))


def _exact_convert_values(wear,weapon_type,passive_types,w_values,p_values):
    if wear not in EXACT_WEAR_MULTIPLIER: raise ValueError(f"Unknown wear: {wear}")
    weapon_ranges = EXACT_WEAPONS[weapon_type]["values"]
    if len(w_values)!=len(weapon_ranges):
        raise ValueError(f"{weapon_type}: expected {len(weapon_ranges)} stats, got {len(w_values)}.")
    w_rarities=[_exact_rarity_from_raw(float(v),lo,hi,wear) for v,(lo,hi) in zip(w_values,weapon_ranges)]
    p_rarities=[]
    for ptype,values in zip(passive_types,p_values):
        ranges=EXACT_PASSIVES[ptype]["values"]
        if ptype=="Living Hive" and len(values)==1: values=[values[0],round(10-values[0],2)]
        if len(values)!=len(ranges):
            raise ValueError(f"{ptype}: expected {len(ranges)} stats, got {len(values)}.")
        p_rarities.append([_exact_rarity_from_raw(float(v),lo,hi,wear) for v,(lo,hi) in zip(values,ranges)])
    return w_rarities,p_rarities


def _parse_boss_exact(block: str) -> str:
    text=block.strip(); compact=" ".join(text.split())
    wear_m=re.search(r"\*\*Wear:\*\*\s*`?(\w+)`?",compact,re.I)
    if not wear_m: raise ValueError("Exact parser could not find Wear.")
    wear=wear_m.group(1).upper()
    animal=_extract_exact_animal(text)
    header_m=re.search(
        r"###\s*(.*?)(?=\*\*Quality:\*\*|\*\*Wear:\*\*|\*\*Type:\*\*"
        r"|\*\*Weapon Cost:\*\*|###\s+__Description__|$)",compact,re.I)
    if not header_m: raise ValueError("Exact parser could not find weapon header.")
    weapon_type=_extract_exact_weapon_type(header_m.group(1))
    all_title_blocks=list(re.finditer(r"\*\*__(.*?)__\*\*",compact))
    real_passive_blocks=[
        (m,_EXACT_PASSIVE_BY_NORM[normalize_name(m.group(1).strip())])
        for m in all_title_blocks if normalize_name(m.group(1).strip()) in _EXACT_PASSIVE_BY_NORM
    ]
    first_passive=real_passive_blocks[0][0] if real_passive_blocks else None
    weapon_section=compact[:first_passive.start()] if first_passive else compact
    wp_cost=None
    if weapon_type not in {"Orb of Potency","Rune of the Forgotten"}:
        wp_m=re.search(r"\*\*Weapon Cost:\*\*\s*(\d+(?:\.\d+)?)",weapon_section,re.I)
        if not wp_m: raise ValueError(f"Exact parser could not find Weapon Cost for {weapon_type}.")
        wp_cost=float(wp_m.group(1))
    weapon_values=_exact_number_values(weapon_section)
    q_m=re.search(r"\*\*Quality:\*\*.*?(\d+(?:\.\d+)?)%",weapon_section,re.I)
    if q_m and weapon_values and abs(weapon_values[0]-float(q_m.group(1)))<0.0001:
        weapon_values.pop(0)
    # Bleeding Gaze now follows the same blueprint ordering as every other
    # normal weapon: displayed weapon values first, Weapon Cost last.
    if weapon_type=="Orb of Potency": w_values=[]
    elif weapon_type=="Rune of the Forgotten": w_values=weapon_values
    else: w_values=weapon_values+[wp_cost]
    passive_types,passive_values=[],[]
    for idx,(m,ptype) in enumerate(real_passive_blocks):
        start=m.end()
        end=real_passive_blocks[idx+1][0].start() if idx+1<len(real_passive_blocks) else len(compact)
        passive_types.append(ptype); passive_values.append(_exact_number_values(compact[start:end]))
    w_rarities,p_rarities=_exact_convert_values(wear,weapon_type,passive_types,w_values,passive_values)
    parts=[animal.lower(), wear.lower(), EXACT_WEAPONS[weapon_type]["alias"]]
    if w_rarities: parts.append(",".join(map(str,w_rarities)))
    for ptype,rvals in zip(passive_types,p_rarities):
        parts.append(EXACT_PASSIVES[ptype]["alias"])
        if rvals: parts.append(",".join(map(str,rvals)))
    return " ".join(p for p in parts if p).strip()


def _parse_boss_fallback(block: str) -> dict:
    compact=" ".join(block.split()); warnings=[]
    header=re.search(r"##\s*Lvl\s*(\d+)\s+\w+\s+(.+?)(?=<:|###|-#|\*\*|$)",compact,re.I)
    if not header: raise ValueError("Could not find boss level/name.")
    level=header.group(1); animal=header.group(2).strip().lower()
    wm=re.search(r"###\s+(?!__Description__)(.+?)(?=\*\*Quality:\*\*|\*\*Wear:\*\*|\*\*Type:\*\*|\*\*Kills:\*\*|###\s+__Description__|$)",compact,re.I)
    if not wm: raise ValueError(f"Could not find weapon for Lvl {level} {animal}.")
    weapon,warn=clean_weapon_name(wm.group(1))
    if warn: warnings.append(warn)
    wear_m=re.search(r"\*\*Wear:\*\*\s*`?(\w+)`?",compact,re.I)
    wear_str=wear_m.group(1).lower() if wear_m else "worn"
    qm=re.search(r"\*\*Quality:\*\*.*?([\d.]+)%",compact,re.I)
    quality=float(qm.group(1)) if qm else 55.0
    passives=[PASSIVE_MAP[normalize_name(t)] for t in re.findall(r"\*\*__([^_]+)__\*\*",compact)
              if normalize_name(t) in PASSIVE_MAP]
    passive_text=" "+" ".join(passives) if passives else ""
    return {"part":f"{level} {animal} {wear_str} {weapon}{passive_text}","quality":quality,"warnings":warnings}


def parse_boss(block: str) -> dict:
    try:
        return {"part":_parse_boss_exact(block),"quality":55.0,"warnings":[],"exact":True}
    except Exception as e:
        fallback=_parse_boss_fallback(block)
        fallback["exact"]=False
        fallback.setdefault("warnings",[]).append("Exact parser fallback: "+str(e))
        return fallback


def build_command(boss_results: list, hp_values: list) -> tuple:
    all_exact=all(b.get("exact") for b in boss_results)
    command=("neon b myself vs "+", ".join(b["part"] for b in boss_results)
             +" -hp "+" ".join(hp_values)+" -m")
    if not all_exact:
        qe=round(sum(b["quality"] for b in boss_results)/len(boss_results))
        command+=f" -qe{qe}"
    return command, [w for b in boss_results for w in b.get("warnings",[])]



# ──────────────────────────────────────────────────────────────
# AUTO-READ SESSION
# ──────────────────────────────────────────────────────────────


class BossSession:
    def __init__(self, user_id: int, channel_id: int):
        self.user_id = user_id
        self.channel_id = channel_id
        self.owo_message_id: int | None = None
        # Store each boss by OwO's own page number, never by click/read order.
        self.page_texts: dict[int, str] = {}
        self.hp_by_page: dict[int, str] = {}
        self.hp_confidence_by_page: dict[int, float] = {}
        self.page_signatures: dict[int, str] = {}
        self.created_at = time.monotonic()

    @property
    def step(self) -> int:
        return len(self.page_texts)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at > SESSION_TIMEOUT_SECONDS


# One active OwO inventory reader per channel. A new valid trigger replaces an old one.
active_sessions: dict[int, BossSession] = {}
STEP_EMOJI = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣"}


async def process_boss_page(
    cog: "BossGenerator",
    channel_id: int,
    message_id: int,
    boss_title: str,
    description: str,
    page_number: int | None,
    detected_hp: str | None = None,
    hp_confidence: float = 0.0,
) -> None:
    session = active_sessions.get(channel_id)
    if session is None:
        return
    if session.expired:
        active_sessions.pop(channel_id, None)
        return

    if session.owo_message_id is None:
        session.owo_message_id = message_id
    elif session.owo_message_id != message_id:
        return

    # Do not fall back to arrival order. Without OwO's page counter, ordering the
    # generated command would be unsafe, so wait for another message update instead.
    if page_number not in (1, 2, 3):
        logger.warning("Could not read page position for %r; ignored", boss_title)
        return

    parse_text = _build_parse_text(boss_title, description)
    signature = hashlib.sha1(parse_text.encode("utf-8", errors="ignore")).hexdigest()

    # Repeated gateway edit events and revisiting a page are common. Ignore the
    # page only when the exact same content is already stored in that slot.
    if session.page_signatures.get(page_number) == signature:
        return

    is_new_page = page_number not in session.page_texts
    session.page_texts[page_number] = parse_text
    session.hp_by_page[page_number] = detected_hp or extract_hp_from_embed(description)
    session.hp_confidence_by_page[page_number] = hp_confidence
    session.page_signatures[page_number] = signature

    action = "captured" if is_new_page else "updated"
    logger.info(
        "Boss page %s: page %s/3, %s (%s/3 unique pages)",
        action, page_number, boss_title, session.step,
    )

    channel = cog.bot.get_channel(channel_id)
    if channel is None:
        return

    # React with the actual OwO page number, not the order in which it was clicked.
    if is_new_page:
        try:
            await channel.get_partial_message(message_id).add_reaction(
                STEP_EMOJI[page_number]
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not add boss-page reaction: %s", exc)

    if all(page in session.page_texts for page in (1, 2, 3)):
        await cog.finish_generator(channel, session)


# ──────────────────────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────────────────────


class BossGenerator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cooldown_config = load_cooldown_config()
        self.cooldown_tasks: dict[int, asyncio.Task] = {}
        self.processed_outcome_messages: set[tuple[int, int]] = set()
        self.guild_boss_watch_tasks: dict[int, asyncio.Task] = {}
        self.guild_boss_fetch_locks: dict[int, asyncio.Lock] = {}
        self.guild_boss_outcome_locks: dict[int, asyncio.Lock] = {}
        self.http_session: aiohttp.ClientSession | None = None
        self.hp_templates = load_hp_templates()
        self._restored = False

    def cog_unload(self) -> None:
        for task in self.cooldown_tasks.values():
            task.cancel()
        self.cooldown_tasks.clear()
        for task in self.guild_boss_watch_tasks.values():
            task.cancel()
        self.guild_boss_watch_tasks.clear()
        if self.http_session and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    # ── Cooldown channel setup + status check ─────────────────

    @app_commands.command(
        name="boss-cooldown-channel",
        description="Choose where automatic guild-boss cooldown alerts are sent.",
    )
    @app_commands.describe(channel="The channel that should receive cooldown and ready alerts")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def boss_cooldown_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ This command only works inside a server.", ephemeral=True)
            return

        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.followup.send("❌ I could not check my channel permissions.", ephemeral=True)
            return

        permissions = channel.permissions_for(bot_member)
        missing: list[str] = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages:
            missing.append("Send Messages")
        if not permissions.embed_links:
            missing.append("Embed Links")

        if missing:
            await interaction.followup.send(
                "❌ I need these permissions in that channel: " + ", ".join(missing),
                ephemeral=True,
            )
            return

        guild_key = str(guild.id)
        config = self.cooldown_config.setdefault(guild_key, {})
        config["channel_id"] = channel.id
        config.setdefault("cooldown_end", 0)
        config.setdefault("last_result", "ready")
        # Remove the old persistent-message field from earlier versions.
        config.pop("message_id", None)
        save_cooldown_config(self.cooldown_config)

        logger.info(
            "Cooldown channel configured for guild %s: #%s (%s)",
            guild.id,
            channel.name,
            channel.id,
        )

        await interaction.followup.send(
            f"✅ Automatic boss cooldown alerts will be sent in {channel.mention}. "
            "Messages will not be pinned.",
            ephemeral=True,
        )

    @app_commands.command(
        name="boss-cooldown",
        description="Check the current guild boss and cooldown status.",
    )
    @app_commands.guild_only()
    async def boss_cooldown(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This command only works inside a server.", ephemeral=True
            )
            return

        config = self.cooldown_config.get(str(guild.id), {})
        if not config.get("channel_id"):
            await interaction.response.send_message(
                "⚠️ No cooldown channel is configured yet. A server manager can use "
                "`/boss-cooldown-channel`.",
                ephemeral=True,
            )
            return

        # Refresh the tracked OwO message immediately so this command does not have
        # to wait for the next 15-second watcher cycle after a defeat or escape.
        await self.refresh_tracked_guild_boss_status(guild.id)
        config = self.cooldown_config.get(str(guild.id), {})

        await interaction.response.send_message(
            embed=self.build_cooldown_embed(config),
            ephemeral=True,
        )

    async def send_prefix_cooldown_status(self, message: discord.Message) -> None:
        """Reply publicly to `H boss cd` / `H boss cooldown`."""
        guild = message.guild
        if guild is None:
            return

        config = self.cooldown_config.get(str(guild.id), {})
        if not config.get("channel_id"):
            await message.reply(
                "⚠️ Boss tracking has not been configured in this server yet. "
                "A server manager can use `/boss-cooldown-channel`.",
                mention_author=False,
            )
            return

        await self.refresh_tracked_guild_boss_status(guild.id)
        config = self.cooldown_config.get(str(guild.id), {})
        await message.reply(
            embed=self.build_cooldown_embed(config),
            mention_author=False,
        )
        logger.info(
            "Prefix cooldown status requested by %s in guild %s",
            message.author,
            guild.id,
        )

    async def send_prefix_help(self, message: discord.Message) -> None:
        """Reply to `H help` with the current command guide."""
        if message.guild is None:
            return

        embed = discord.Embed(
            title="🐾 OwO Boss Helper",
            description=(
                "`H` stands for **Helper** — and it is also the first letter of "
                "Hassaan's name. This guide lists the commands currently supported."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="⚔️ Boss command generator",
            value=(
                "Send `owo boss i` or `w boss i`, then open pages `1/3`, `2/3`, "
                "and `3/3`."
            ),
            inline=False,
        )
        embed.add_field(
            name="⏱️ Guild-boss status",
            value=(
                "Use `H boss cd`, `H boss cooldown`, or `/boss-cooldown`. Managers "
                "configure alerts with `/boss-cooldown-channel`."
            ),
            inline=False,
        )
        embed.add_field(
            name="🎟️ Boss tickets",
            value=(
                "Update with `owo boss t` / `w boss t`; view with `H boss t`, "
                "`H boss list`, `HBL`, or `/boss-ticket-list`. Managers use "
                "`/boss-ticket-channel` and `/boss-ticket-manage`."
            ),
            inline=False,
        )
        embed.add_field(
            name="💾 Team templates",
            value=(
                "Use `HT C <name>` to save, `HT` or `HT<number>` to open, "
                "`HT U <slot/name>` to update, `HT D <slot/name>` to delete, and "
                "`HT help` for the full guide."
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Project",
            value="Use `H about` or `/about` for developer and project information.",
            inline=False,
        )
        embed.set_footer(text="Use H help anytime to show this current command guide.")

        await message.reply(embed=embed, mention_author=False)
        logger.info(
            "Prefix help requested by %s in guild %s",
            message.author,
            message.guild.id,
        )

    @boss_cooldown_channel.error
    async def boss_cooldown_channel_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = f"❌ Could not set the cooldown channel: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    # ── Startup / restoration ──────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored:
            return
        self._restored = True
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
        await self.restore_cooldowns()
        self.restore_guild_boss_watchers()
        logger.info(
            "Restored cooldown state for %s configured guild(s)",
            len(self.cooldown_config),
        )

    async def restore_cooldowns(self) -> None:
        """Resume active timers and announce readiness once if one ended offline."""
        now = int(time.time())
        changed = False

        for guild_key, config in list(self.cooldown_config.items()):
            try:
                guild_id = int(guild_key)
                cooldown_end = int(config.get("cooldown_end") or 0)
            except (TypeError, ValueError):
                continue

            # Clean up and unpin the old persistent status message from earlier versions.
            legacy_message_id = int(config.get("message_id") or 0)
            if legacy_message_id:
                try:
                    await self.unpin_legacy_status_message(guild_id, legacy_message_id)
                except Exception as exc:
                    logger.warning("Could not unpin old status message: %s", exc)
            if "message_id" in config:
                config.pop("message_id", None)
                changed = True

            # Older builds incorrectly created a five-minute cooldown after an
            # escape. Migrate that state to immediately ready without sending a
            # delayed or misleading cooldown-complete alert.
            if str(config.get("last_result") or "") == "escaped" and cooldown_end > 0:
                config["cooldown_end"] = 0
                changed = True
                logger.info("Removed legacy escape cooldown for guild %s", guild_id)
            elif cooldown_end > now:
                self.schedule_ready_update(guild_id, cooldown_end)
            elif cooldown_end > 0:
                # The bot was offline when a defeat cooldown ended. Mark it ready
                # and send the missed ready alert once after reconnecting.
                config["cooldown_end"] = 0
                config["last_result"] = "ready"
                changed = True
                try:
                    await self.send_ready_message(guild_id)
                except Exception as exc:
                    logger.warning("Could not restore cooldown for guild %s: %s", guild_id, exc)

        if changed:
            save_cooldown_config(self.cooldown_config)

    async def detect_hp_from_raw(self, data: dict[str, Any]) -> tuple[str | None, float]:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
        for url in extract_media_urls(data):
            try:
                async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        continue
                    body = await response.read()
                hp, confidence = read_hp_from_image_bytes(body, self.hp_templates)
                if hp is not None:
                    logger.info("HP image detected %s (confidence %.2f)", hp, confidence)
                    return hp, confidence
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
        return None, 0.0

    def is_spawn_window_open(self, guild_id: int) -> bool:
        """True only when the guild can legitimately receive a new boss."""
        if not self.is_cooldown_configured(guild_id):
            return False
        config = self.cooldown_config.get(str(guild_id), {})
        if config.get("active_boss_message_id"):
            return False
        return int(config.get("cooldown_end") or 0) <= int(time.time())

    def should_inspect_guild_boss_gateway(self, guild_id: int) -> bool:
        """Inspect gateway payloads only while active or able to spawn.

        During a defeat cooldown no boss can appear, so unrelated OwO traffic is
        ignored completely. While a boss is active, gateway payloads are still
        inspected cheaply so a newer status card can replace the tracked one.
        """
        if not self.is_cooldown_configured(guild_id):
            return False
        config = self.cooldown_config.get(str(guild_id), {})
        if config.get("active_boss_message_id"):
            return True
        return self.is_spawn_window_open(guild_id)

    def is_tracked_boss_message(
        self, guild_id: int, channel_id: int, message_id: int
    ) -> bool:
        config = self.cooldown_config.get(str(guild_id), {})
        return (
            int(config.get("active_boss_channel_id") or 0) == channel_id
            and int(config.get("active_boss_message_id") or 0) == message_id
        )

    def get_guild_boss_fetch_lock(self, guild_id: int) -> asyncio.Lock:
        return self.guild_boss_fetch_locks.setdefault(guild_id, asyncio.Lock())

    def get_guild_boss_outcome_lock(self, guild_id: int) -> asyncio.Lock:
        """Serialize defeat/escape handling so one boss emits one alert."""
        return self.guild_boss_outcome_locks.setdefault(guild_id, asyncio.Lock())

    def clear_active_boss_tracking(self, guild_id: int, reason: str) -> None:
        """Clear stale active-message fields without changing the known outcome."""
        config = self.cooldown_config.setdefault(str(guild_id), {})
        changed = False
        for key in (
            "active_boss_channel_id",
            "active_boss_message_id",
            "active_boss_expires_at",
            "active_boss_unverified",
        ):
            if key in config:
                config.pop(key, None)
                changed = True
        watcher = self.guild_boss_watch_tasks.get(guild_id)
        if watcher and watcher is not asyncio.current_task():
            watcher.cancel()
        if changed:
            save_cooldown_config(self.cooldown_config)
            logger.info("Cleared guild %s stale active boss state: %s", guild_id, reason)

    async def refresh_tracked_guild_boss_status(self, guild_id: int) -> None:
        """Reconcile the tracked boss before displaying a manual status check."""
        config = self.cooldown_config.get(str(guild_id), {})
        channel_id = int(config.get("active_boss_channel_id") or 0)
        message_id = int(config.get("active_boss_message_id") or 0)
        if not channel_id or not message_id:
            return

        now = int(time.time())
        expiry = int(config.get("active_boss_expires_at") or 0)
        last_boss_key = int(config.get("last_boss_key") or 0)
        last_result = str(config.get("last_result") or "")

        # A late copy of the completed boss card must never reactivate the boss.
        if (
            expiry
            and expiry == last_boss_key
            and last_result in {"defeated", "escaped", "ready"}
        ):
            self.clear_active_boss_tracking(
                guild_id,
                f"completed boss {expiry} was still marked active",
            )
            return

        # The watcher, H command, and slash command can fire close together. One
        # per-guild lock prevents duplicate GETs for the same tracked message.
        async with self.get_guild_boss_fetch_lock(guild_id):
            data = await fetch_raw_message(self.bot, channel_id, message_id)

        if not data:
            if expiry and expiry <= now:
                await self.finish_boss_escape(
                    guild_id,
                    message_id,
                    expiry,
                    boss_key=expiry,
                )
                return
            config = self.cooldown_config.setdefault(str(guild_id), {})
            if not config.get("active_boss_unverified"):
                config["active_boss_unverified"] = True
                save_cooldown_config(self.cooldown_config)
            return

        if int((data.get("author") or {}).get("id", 0)) != OWO_BOT_ID:
            config = self.cooldown_config.setdefault(str(guild_id), {})
            config["active_boss_unverified"] = True
            save_cooldown_config(self.cooldown_config)
            return

        await self.track_latest_guild_boss_message(
            guild_id,
            channel_id,
            message_id,
            data,
        )

        config = self.cooldown_config.get(str(guild_id), {})
        expiry = int(config.get("active_boss_expires_at") or 0)
        if expiry and expiry <= int(time.time()) and config.get("active_boss_message_id"):
            await self.finish_boss_escape(
                guild_id,
                message_id,
                expiry,
                boss_key=expiry,
            )

    async def track_latest_guild_boss_message(self, guild_id: int, channel_id: int, message_id: int, data: dict[str, Any]) -> None:
        if not self.is_cooldown_configured(guild_id) or not is_guild_boss_status(data):
            return

        config = self.cooldown_config.setdefault(str(guild_id), {})
        old_id = int(config.get("active_boss_message_id") or 0)
        old_channel_id = int(config.get("active_boss_channel_id") or 0)
        if old_id and message_id < old_id:
            return

        outcome = detect_boss_outcome(extract_all_text_from_raw(data))
        if outcome is not None:
            # A completed status may be a newer replacement message. Let the
            # outcome handler apply the correct rule from OwO's timestamp: five
            # minutes after defeat, or immediate readiness after escape.
            await self.maybe_handle_outcome(guild_id, message_id, data)
            return

        now = int(time.time())
        expiry = extract_future_boss_expiry(data, now)
        previous_expiry = int(config.get("active_boss_expires_at") or 0)
        last_boss_key = int(config.get("last_boss_key") or 0)
        last_result = str(config.get("last_result") or "")

        # Ignore late active cards belonging to a boss whose defeat/escape was
        # already processed. Without this guard, an old status edit can make
        # `H boss cd` incorrectly report an active boss again.
        if (
            expiry
            and expiry == last_boss_key
            and last_result in {"defeated", "escaped", "ready"}
        ):
            logger.info(
                "Ignored late active card %s for completed guild %s boss %s",
                message_id,
                guild_id,
                expiry,
            )
            return

        if expiry and expiry <= now:
            await self.finish_boss_escape(
                guild_id,
                message_id,
                expiry,
                boss_key=expiry,
            )
            return
        is_new_boss = bool(expiry and expiry != previous_expiry)
        tracking_changed = (
            old_id != message_id
            or old_channel_id != channel_id
            or bool(expiry and expiry != previous_expiry)
        )

        config["active_boss_channel_id"] = channel_id
        config["active_boss_message_id"] = message_id
        config.pop("active_boss_unverified", None)
        if expiry:
            config["active_boss_expires_at"] = expiry

        # A newly active boss supersedes any stale ready/cooldown state.
        if is_new_boss:
            config["cooldown_end"] = 0
            config["last_result"] = "active"
            old_task = self.cooldown_tasks.pop(guild_id, None)
            if old_task:
                old_task.cancel()

        if tracking_changed or is_new_boss:
            save_cooldown_config(self.cooldown_config)
        self.start_guild_boss_watcher(guild_id)

        if tracking_changed:
            logger.info(
                "Tracking guild %s boss message %s in channel %s%s",
                guild_id,
                message_id,
                channel_id,
                f"; escapes at {expiry}" if expiry else "",
            )

        if is_new_boss and int(config.get("announced_boss_expires_at") or 0) != expiry:
            config["announced_boss_expires_at"] = expiry
            save_cooldown_config(self.cooldown_config)
            await self.send_new_boss_message(guild_id, expiry)

    def restore_guild_boss_watchers(self) -> None:
        for guild_key, config in self.cooldown_config.items():
            if config.get("active_boss_channel_id") and config.get("active_boss_message_id"):
                self.start_guild_boss_watcher(int(guild_key))

    def start_guild_boss_watcher(self, guild_id: int) -> None:
        existing = self.guild_boss_watch_tasks.get(guild_id)
        if existing and not existing.done():
            return
        self.guild_boss_watch_tasks[guild_id] = asyncio.create_task(self.watch_latest_guild_boss(guild_id))

    async def watch_latest_guild_boss(self, guild_id: int) -> None:
        try:
            # Restored guilds otherwise begin polling in the same millisecond. A
            # small deterministic stagger spreads those first requests out.
            await asyncio.sleep((guild_id % 7) * 0.35)
            while True:
                config = self.cooldown_config.get(str(guild_id), {})
                channel_id = int(config.get("active_boss_channel_id") or 0)
                message_id = int(config.get("active_boss_message_id") or 0)
                if not channel_id or not message_id:
                    return
                async with self.get_guild_boss_fetch_lock(guild_id):
                    data = await fetch_raw_message(self.bot, channel_id, message_id)

                if not data:
                    # OwO status cards can be deleted or replaced. Do not keep
                    # requesting a missing message every 15 seconds. Pause REST
                    # polling while gateway events remain able to replace the
                    # tracked ID, and fall back to the stored escape timestamp.
                    logger.info(
                        "Tracked guild %s boss message %s is unavailable; "
                        "pausing REST polling until replacement or expiry",
                        guild_id,
                        message_id,
                    )
                    while True:
                        await asyncio.sleep(BOSS_WATCH_INTERVAL_SECONDS)
                        current_config = self.cooldown_config.get(str(guild_id), {})
                        current_message_id = int(
                            current_config.get("active_boss_message_id") or 0
                        )
                        if not current_message_id:
                            return
                        if current_message_id != message_id:
                            break
                        expiry = int(
                            current_config.get("active_boss_expires_at") or 0
                        )
                        if expiry and expiry <= int(time.time()):
                            await self.finish_boss_escape(
                                guild_id,
                                message_id,
                                expiry,
                                boss_key=expiry,
                            )
                            return
                    continue

                if int((data.get("author") or {}).get("id", 0)) == OWO_BOT_ID:
                    await self.track_latest_guild_boss_message(
                        guild_id,
                        channel_id,
                        message_id,
                        data,
                    )
                    config = self.cooldown_config.get(str(guild_id), {})
                    if not config.get("active_boss_message_id"):
                        return

                    # If OwO has not edited the card yet, the stored future expiry
                    # still gives us an authoritative escape time. Mark the guild
                    # ready immediately at that time instead of waiting for another
                    # user to request the boss status.
                    expiry = int(config.get("active_boss_expires_at") or 0)
                    if expiry and expiry <= int(time.time()):
                        await self.finish_boss_escape(
                            guild_id,
                            message_id,
                            expiry,
                            boss_key=expiry,
                        )
                        return
                await asyncio.sleep(BOSS_WATCH_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return
        finally:
            current = self.guild_boss_watch_tasks.get(guild_id)
            if current is asyncio.current_task():
                self.guild_boss_watch_tasks.pop(guild_id, None)

    # ── Gateway listeners ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            # Human trigger: only exact owo-boss-inventory forms.
            if not message.author.bot:
                if message.guild and is_prefix_help_trigger(message.content):
                    await self.send_prefix_help(message)
                    return

                if message.guild and is_prefix_cooldown_trigger(message.content):
                    await self.send_prefix_cooldown_status(message)
                    return

                if message.guild and is_boss_trigger(message.content):
                    active_sessions[message.channel.id] = BossSession(
                        user_id=message.author.id,
                        channel_id=message.channel.id,
                    )
                    logger.info(
                        "Boss reader armed by %s in #%s",
                        message.author,
                        getattr(message.channel, "name", message.channel.id),
                    )
                return

            if message.author.id != OWO_BOT_ID or message.guild is None:
                return

            generator_needed = message.channel.id in active_sessions
            cooldown_gateway_needed = self.should_inspect_guild_boss_gateway(
                message.guild.id
            )
            if not generator_needed and not cooldown_gateway_needed:
                return

            # Message-create events already contain Components V2. Build raw-like
            # data locally rather than GETting every OwO response from Discord.
            data = message_to_raw_data(message)

            if cooldown_gateway_needed and is_guild_boss_status(data):
                await self.track_latest_guild_boss_message(
                    message.guild.id, message.channel.id, message.id, data
                )

            if generator_needed:
                boss_title, description = extract_boss_from_raw(data)
                page_number = extract_boss_page_number(data)

                # Older discord.py builds may not expose every Components V2 field
                # on Message. Fall back to one REST fetch only for an explicitly
                # armed three-page generator session, never for general grinding.
                if not boss_title or page_number is None:
                    fetched = await fetch_raw_message(
                        self.bot, message.channel.id, message.id
                    )
                    if fetched:
                        data = fetched
                        boss_title, description = extract_boss_from_raw(data)
                        page_number = extract_boss_page_number(data)

                if boss_title:
                    detected_hp, hp_confidence = await self.detect_hp_from_raw(data)
                    await process_boss_page(
                        self,
                        message.channel.id,
                        message.id,
                        boss_title,
                        description,
                        page_number,
                        detected_hp,
                        hp_confidence,
                    )

        except Exception as exc:
            logger.exception("Unhandled error in on_message: %s", exc)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        try:
            if payload.guild_id is None:
                return

            generator_needed = payload.channel_id in active_sessions
            cooldown_gateway_needed = self.should_inspect_guild_boss_gateway(
                payload.guild_id
            )
            if not generator_needed and not cooldown_gateway_needed:
                return

            # For cooldown discovery/tracking, use the edit payload directly. If an
            # update is partial, the single-message 15-second watcher will read the
            # final state. We deliberately do not REST-fetch every OwO edit.
            if cooldown_gateway_needed:
                data = dict(payload.data)
                author_id = int((data.get("author") or {}).get("id", 0) or 0)
                tracked = self.is_tracked_boss_message(
                    payload.guild_id, payload.channel_id, payload.message_id
                )
                if (tracked or author_id == OWO_BOT_ID) and is_guild_boss_status(data):
                    await self.track_latest_guild_boss_message(
                        payload.guild_id,
                        payload.channel_id,
                        payload.message_id,
                        data,
                    )

            # Page navigation edits are intentionally fetched because the user has
            # explicitly armed a short generator session. This is a tiny, bounded
            # request count and is unrelated to server-wide grinding traffic.
            if generator_needed:
                data = await fetch_raw_message(
                    self.bot, payload.channel_id, payload.message_id
                )
                if not data:
                    return
                if int((data.get("author") or {}).get("id", 0)) != OWO_BOT_ID:
                    return

                boss_title, description = extract_boss_from_raw(data)
                if boss_title:
                    page_number = extract_boss_page_number(data)
                    detected_hp, hp_confidence = await self.detect_hp_from_raw(data)
                    await process_boss_page(
                        self,
                        payload.channel_id,
                        payload.message_id,
                        boss_title,
                        description,
                        page_number,
                        detected_hp,
                        hp_confidence,
                    )

        except Exception as exc:
            logger.exception("Unhandled error in on_raw_message_edit: %s", exc)

    # ── Generator result ───────────────────────────────────────

    async def finish_generator(self, channel: discord.abc.Messageable, session: BossSession) -> None:
        active_sessions.pop(session.channel_id, None)

        blocks: list[str] = []
        missing_pages: list[int] = []
        for page_number in (1, 2, 3):
            text = session.page_texts.get(page_number)
            if not text:
                missing_pages.append(page_number)
                continue
            found = split_boss_blocks(text)
            if found:
                blocks.append(found[0])
            else:
                missing_pages.append(page_number)

        if missing_pages or len(blocks) != 3:
            missing_text = ", ".join(f"{page}/3" for page in missing_pages) or "unknown"
            await channel.send(
                f"⚠️ I could not capture every boss page. Missing: **{missing_text}**. "
                "Run `owo boss i` or `w boss i` again and open all three pages."
            )
            return

        try:
            # Both bosses and HP values are always emitted in OwO's 1/3 → 2/3 → 3/3 order.
            boss_results = [parse_boss(block) for block in blocks]
            hp_values = [session.hp_by_page.get(page, DEFAULT_HP) for page in (1, 2, 3)]
            command, warnings = build_command(boss_results, hp_values)
        except Exception as exc:
            await channel.send(f"❌ I could not build the boss command: `{exc}`")
            return

        # Send the command as normal message content rather than inside an embed.
        # Inline code in a regular message is much easier to copy on Discord mobile.
        await channel.send(f"`{command}`")
        if warnings:
            await channel.send(
                "⚠️ **Parser note**\n"
                + "\n".join(f"• {warning}" for warning in warnings[:4])
            )
        logger.info(
            "Generated boss command for user %s in channel %s with HP %s",
            session.user_id,
            session.channel_id,
            ",".join(hp_values),
        )

    # ── Cooldown detection / persistence ───────────────────────

    def is_cooldown_configured(self, guild_id: int) -> bool:
        config = self.cooldown_config.get(str(guild_id), {})
        return bool(config.get("channel_id"))

    async def maybe_handle_outcome(
        self,
        guild_id: int,
        source_message_id: int,
        data: dict[str, Any],
    ) -> None:
        message_key = (guild_id, source_message_id)
        if message_key in self.processed_outcome_messages:
            return

        outcome = detect_boss_outcome(extract_all_text_from_raw(data))
        if outcome is None:
            return

        now = int(time.time())
        event_time = extract_relevant_timestamp(data, now) or now

        # The tracked escape timestamp is a stable identity for the current boss.
        # OwO can publish/edit more than one result message for the same boss, and
        # those messages do not always expose exactly the same result timestamp.
        config = self.cooldown_config.get(str(guild_id), {})
        boss_key = int(config.get("active_boss_expires_at") or 0) or event_time

        if outcome == "escaped":
            await self.finish_boss_escape(
                guild_id,
                source_message_id,
                event_time,
                boss_key=boss_key,
            )
        else:
            await self.start_defeat_cooldown(
                guild_id,
                source_message_id,
                event_time,
                boss_key=boss_key,
            )

    def claim_boss_outcome(
        self,
        guild_id: int,
        source_message_id: int,
        outcome: str,
        event_time: int,
        boss_key: int,
    ) -> tuple[dict[str, Any], int] | None:
        """Deduplicate an outcome, clear the active boss, and persist its result."""
        message_key = (guild_id, source_message_id)
        config = self.cooldown_config.setdefault(str(guild_id), {})
        now = int(time.time())

        if int(config.get("last_source_message_id") or 0) == source_message_id:
            self.processed_outcome_messages.add(message_key)
            return None

        previous_boss_key = int(config.get("last_boss_key") or 0)
        if boss_key and previous_boss_key == boss_key:
            self.processed_outcome_messages.add(message_key)
            logger.info(
                "Ignored duplicate %s result for guild %s boss key %s",
                outcome,
                guild_id,
                boss_key,
            )
            return None

        # If a new boss appeared while an older result was settling, never clear
        # or announce the stale result over the newer active boss.
        current_boss_key = int(config.get("active_boss_expires_at") or 0)
        if current_boss_key and boss_key and current_boss_key != boss_key:
            self.processed_outcome_messages.add(message_key)
            logger.info(
                "Ignored stale %s result for guild %s boss key %s; current key is %s",
                outcome,
                guild_id,
                boss_key,
                current_boss_key,
            )
            return None

        # OwO can publish the same result through more than one replacement
        # message. Deduplicate only the same outcome at essentially the same event
        # time. Do not use a global time lock: after an escape, a new boss may
        # appear immediately and could legitimately end within a few seconds.
        previous_outcome = str(config.get("last_result") or "")
        previous_event_time = int(config.get("last_outcome_event_time") or 0)
        if (
            previous_outcome == outcome
            and previous_event_time
            and abs(event_time - previous_event_time) <= OUTCOME_DEDUP_SECONDS
        ):
            self.processed_outcome_messages.add(message_key)
            return None

        self.processed_outcome_messages.add(message_key)
        if len(self.processed_outcome_messages) > 2000:
            self.processed_outcome_messages.clear()
            self.processed_outcome_messages.add(message_key)

        config["last_result"] = outcome
        config["last_detected_at"] = now
        config["last_outcome_event_time"] = event_time
        config["last_source_message_id"] = source_message_id
        config["last_boss_key"] = boss_key
        config.pop("message_id", None)
        config.pop("active_boss_channel_id", None)
        config.pop("active_boss_message_id", None)
        config.pop("active_boss_expires_at", None)
        config.pop("active_boss_unverified", None)

        watcher = self.guild_boss_watch_tasks.get(guild_id)
        if watcher and watcher is not asyncio.current_task():
            watcher.cancel()

        return config, now

    async def start_defeat_cooldown(
        self,
        guild_id: int,
        source_message_id: int,
        event_time: int,
        *,
        boss_key: int | None = None,
    ) -> None:
        """Start the five-minute cooldown that follows a defeated guild boss."""
        # OwO can emit several edits/replacement messages almost simultaneously.
        # Let that burst settle, then serialize the final outcome per guild.
        await asyncio.sleep(OUTCOME_SETTLE_SECONDS)
        async with self.get_guild_boss_outcome_lock(guild_id):
            effective_boss_key = int(boss_key or 0) or event_time
            claimed = self.claim_boss_outcome(
                guild_id,
                source_message_id,
                "defeated",
                event_time,
                effective_boss_key,
            )
            if claimed is None:
                return

            config, now = claimed
            cooldown_end = event_time + BOSS_COOLDOWN_SECONDS
            config["cooldown_end"] = cooldown_end
            save_cooldown_config(self.cooldown_config)

            # Ignore an old result whose five-minute defeat cooldown already ended.
            if cooldown_end <= now:
                config["cooldown_end"] = 0
                config["last_result"] = "ready"
                save_cooldown_config(self.cooldown_config)
                logger.info("Ignored old defeated result from %s", event_time)
                return

            logger.info(
                "Guild %s boss defeated; cooldown ends at %s",
                guild_id,
                cooldown_end,
            )
            await self.send_cooldown_started_message(guild_id)
            self.schedule_ready_update(guild_id, cooldown_end)

    async def finish_boss_escape(
        self,
        guild_id: int,
        source_message_id: int,
        event_time: int,
        *,
        boss_key: int | None = None,
    ) -> None:
        """Mark the guild ready immediately because escapes have no cooldown."""
        # Wait briefly for OwO's edit burst to settle, then let exactly one task
        # claim and announce this boss outcome.
        await asyncio.sleep(OUTCOME_SETTLE_SECONDS)
        async with self.get_guild_boss_outcome_lock(guild_id):
            effective_boss_key = int(boss_key or 0) or event_time
            claimed = self.claim_boss_outcome(
                guild_id,
                source_message_id,
                "escaped",
                event_time,
                effective_boss_key,
            )
            if claimed is None:
                return

            config, now = claimed
            config["cooldown_end"] = 0

            old_task = self.cooldown_tasks.pop(guild_id, None)
            if old_task:
                old_task.cancel()

            save_cooldown_config(self.cooldown_config)

            # Do not publish a fresh alert for a very old escaped card encountered
            # during history restoration or after a long offline period.
            if now - event_time > BOSS_COOLDOWN_SECONDS:
                logger.info("Ignored old escaped result from %s", event_time)
                return

            logger.info(
                "Guild %s boss escaped at %s; no cooldown applies",
                guild_id,
                event_time,
            )
            await self.send_escape_ready_message(guild_id)

    def schedule_ready_update(self, guild_id: int, cooldown_end: int) -> None:
        old_task = self.cooldown_tasks.pop(guild_id, None)
        if old_task:
            old_task.cancel()
        self.cooldown_tasks[guild_id] = asyncio.create_task(
            self.finish_cooldown_when_ready(guild_id, cooldown_end)
        )

    async def finish_cooldown_when_ready(self, guild_id: int, expected_end: int) -> None:
        try:
            await asyncio.sleep(max(0, expected_end - time.time()))
            config = self.cooldown_config.get(str(guild_id), {})
            if int(config.get("cooldown_end") or 0) != expected_end:
                return

            config["cooldown_end"] = 0
            config["last_result"] = "ready"
            save_cooldown_config(self.cooldown_config)
            await self.send_ready_message(guild_id)
        except asyncio.CancelledError:
            return
        finally:
            current = self.cooldown_tasks.get(guild_id)
            if current is asyncio.current_task():
                self.cooldown_tasks.pop(guild_id, None)

    def build_cooldown_embed(self, config: dict[str, Any]) -> discord.Embed:
        now = int(time.time())
        cooldown_end = int(config.get("cooldown_end") or 0)
        active_expiry = int(config.get("active_boss_expires_at") or 0)
        result = str(config.get("last_result") or "ready")
        unverified = bool(config.get("active_boss_unverified"))

        if cooldown_end > now:
            embed = discord.Embed(
                title="⏳ Guild Boss Cooldown",
                description=(
                    "The guild boss was **defeated**.\n\n"
                    f"**Next boss cooldown ends:** <t:{cooldown_end}:R>\n"
                    f"**Ready at:** <t:{cooldown_end}:F>"
                ),
                color=0xFEE75C,
            )
            embed.set_footer(
                text="Discord displays the time correctly for each member's timezone."
            )
            return embed

        if config.get("active_boss_message_id") and unverified:
            if active_expiry > now:
                timing = (
                    f"\n\n**Last known escape time:** <t:{active_expiry}:R>\n"
                    f"**Exact time:** <t:{active_expiry}:F>"
                )
            else:
                timing = ""
            return discord.Embed(
                title="❔ Guild Boss Status Unconfirmed",
                description=(
                    "The last tracked OwO boss message is no longer available, so the "
                    "helper will not claim that the boss is still active. Run an OwO "
                    "boss-status command or continue grinding; the next status card will "
                    f"refresh this automatically.{timing}"
                ),
                color=0xFEE75C,
            )

        if config.get("active_boss_message_id"):
            if active_expiry > now:
                description = (
                    "A guild boss is currently active.\n\n"
                    f"**The boss will escape:** <t:{active_expiry}:R>\n"
                    f"**Escape time:** <t:{active_expiry}:F>\n\n"
                    "If the boss is defeated first, the five-minute cooldown starts "
                    "from the defeat time."
                )
            else:
                description = (
                    "A guild boss status is being tracked, but its exact escape time "
                    "is not available yet. The helper will update when OwO publishes "
                    "a complete status card."
                )
            return discord.Embed(
                title="⚔️ Guild Boss Active",
                description=description,
                color=0x5865F2,
            )

        if result == "escaped":
            return discord.Embed(
                title="✅ No Active Guild Boss",
                description=(
                    "The previous guild boss **escaped**. There is no cooldown after an "
                    "escape, and no new boss has been detected yet. Keep grinding to "
                    "spawn the next guild boss."
                ),
                color=0x57F287,
            )

        if result in {"defeated", "ready"}:
            return discord.Embed(
                title="✅ Guild Boss Ready",
                description=(
                    "There is currently no confirmed guild boss or cooldown. The previous "
                    "defeat cooldown has ended, so keep grinding to spawn a new boss."
                ),
                color=0x57F287,
            )

        return discord.Embed(
            title="✅ No Active Guild Boss",
            description=(
                "There is currently no confirmed guild boss or cooldown. Keep grinding "
                "to spawn a new guild boss."
            ),
            color=0x57F287,
        )

    async def get_configured_channel(self, guild_id: int) -> discord.TextChannel | None:
        config = self.cooldown_config.get(str(guild_id), {})
        channel_id = int(config.get("channel_id") or 0)
        if not channel_id:
            return None

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None

        return channel if isinstance(channel, discord.TextChannel) else None

    async def unpin_legacy_status_message(
        self, guild_id: int, message_id: int
    ) -> None:
        """Unpin the status message created by the previous bot version."""
        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(message_id)
            if message.pinned:
                await message.unpin(reason="OwO cooldown tracker no longer uses pinned messages")
        except discord.NotFound:
            return
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Old status message could not be unpinned automatically: %s", exc)

    async def send_cooldown_started_message(self, guild_id: int) -> None:
        config = self.cooldown_config.get(str(guild_id))
        if not config:
            return

        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            logger.warning("Configured cooldown channel for guild %s is unavailable", guild_id)
            return

        try:
            await channel.send(embed=self.build_cooldown_embed(config))
            logger.info("Sent cooldown alert for guild %s", guild_id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send cooldown alert: %s", exc)

    async def send_escape_ready_message(self, guild_id: int) -> None:
        """Announce that an escaped boss can be replaced immediately."""
        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            logger.warning(
                "Configured cooldown channel for guild %s is unavailable", guild_id
            )
            return

        try:
            await channel.send(
                embed=discord.Embed(
                    title="🏃 Guild Boss Escaped",
                    description=(
                        "The guild boss escaped. There is **no cooldown after an "
                        "escape**, so a new guild boss can appear immediately."
                    ),
                    color=0x57F287,
                )
            )
            logger.info("Sent boss-escaped ready alert for guild %s", guild_id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send boss-escaped alert: %s", exc)

    async def send_new_boss_message(self, guild_id: int, expiry: int | None) -> None:
        """Announce a newly detected guild boss once in the configured channel."""
        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            logger.warning("Configured cooldown channel for guild %s is unavailable", guild_id)
            return

        description = (
            "A new guild boss has appeared!\n\n"
            "Use `owo boss i` or `w boss i` to let the helper read your three "
            "bosses and generate the Neon battle command."
        )
        if expiry:
            description += (
                f"\n\n**The boss will escape:** <t:{expiry}:R>\n"
                f"**Escape time:** <t:{expiry}:F>"
            )

        try:
            await channel.send(
                embed=discord.Embed(
                    title="⚔️ New Guild Boss Appeared",
                    description=description,
                    color=0x5865F2,
                )
            )
            logger.info("Announced new guild boss in guild %s", guild_id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send new-boss alert: %s", exc)

    async def send_ready_message(self, guild_id: int) -> None:
        config = self.cooldown_config.get(str(guild_id), {})
        if config.get("active_boss_message_id"):
            # A new boss is already running, so a generic "ready to spawn" alert
            # would be misleading.
            return

        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            logger.warning("Configured cooldown channel for guild %s is unavailable", guild_id)
            return

        try:
            await channel.send(
                embed=discord.Embed(
                    title="✅ Guild Boss Ready",
                    description=(
                        "The 5-minute cooldown has ended. "
                        "A new guild boss can now appear."
                    ),
                    color=0x57F287,
                )
            )
            logger.info("Sent boss-ready alert for guild %s", guild_id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send ready alert: %s", exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BossGenerator(bot))
