"""
cogs/boss_generator.py — automatic OwO boss command generator and cooldown tracker.

Generator:
- Watches only exact OwO boss-inventory commands after whitespace is removed:
  `owobossi` and `wbossi` (so `owo boss i`, `owoboss i`, `w boss i`, etc. work).
- Reads the three paginated OwO boss cards, orders them by the visible 1/3–3/3 counter, and posts the Neon battle command.

Cooldown tracker:
- Watches the official OwO Bot across the server.
- Reads OwO's outcome timestamp and ends cooldown exactly 5 minutes after it.
- Sends one cooldown-start message and one ready message in the selected channel using Discord timestamps.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


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

SESSION_TIMEOUT_SECONDS = 180
BOSS_COOLDOWN_SECONDS = 5 * 60
OUTCOME_DEDUP_SECONDS = 20

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOLDOWN_CONFIG_FILE = PROJECT_ROOT / "boss_cooldown_config.json"


def is_boss_trigger(content: str) -> bool:
    """Accept only `owo boss i` or `w boss i`, with any whitespace/capitalization."""
    normalized = re.sub(r"\s+", "", content or "").lower()
    return normalized in ALLOWED_BOSS_TRIGGERS


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


# Outcome detection is intentionally conservative. Guild-boss cards can contain
# counters such as "0 defeated", which are statistics rather than an outcome.
# We prefer OwO's own Discord timestamp beside "expired"/"defeated" and only
# fall back to a recent message/edit timestamp for clear result sentences.
DISCORD_TIMESTAMP_RE = re.compile(r"<t:(\d{9,12})(?::[tTdDfFR])?>")
COUNTER_TEXT_RE = re.compile(r"\b\d+\s+(?:fighters?|defeated)\b", re.I)

DEFEATED_PATTERNS = (
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,120}\b(?:has\s+been\s+|was\s+)?(?:defeated|slain|killed)\b", re.I),
    re.compile(r"\b(?:defeated|slain|killed)\b.{0,120}\b(?:the\s+|guild\s+)?boss\b", re.I),
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,120}\bhas\s+fallen\b", re.I),
)

ESCAPED_PATTERNS = (
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,120}\b(?:has\s+|was\s+)?(?:escaped|expired|fled|ran\s+away)\b", re.I),
    re.compile(r"\b(?:escaped|expired|fled|ran\s+away)\b.{0,120}\b(?:the\s+|guild\s+)?boss\b", re.I),
    re.compile(r"\b(?:the\s+|guild\s+)?boss\b.{0,120}\bgot\s+away\b", re.I),
)

STATUS_WORD_RE = re.compile(
    r"\b(expired|escaped|fled|ran\s+away|defeated|slain|killed)\b",
    re.I,
)

# A real outcome gateway event should be very close to the message's creation or
# edit time. This fallback is only used when OwO does not include a visible <t:...>
# marker. Old cards are ignored instead of receiving a fresh five-minute timer.
RECENT_OUTCOME_FALLBACK_SECONDS = 10 * 60


def _iter_string_values(node: Any):
    """Yield every string recursively from a raw Discord message payload."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_string_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_string_values(value)


def _parse_iso_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _status_timestamp_candidates(data: dict[str, Any]) -> list[tuple[int, str, int]]:
    """Return (distance, outcome, timestamp) candidates from OwO's visible text."""
    candidates: list[tuple[int, str, int]] = []

    def scan(raw_value: str) -> None:
        status_matches = list(STATUS_WORD_RE.finditer(raw_value))
        timestamp_matches = list(DISCORD_TIMESTAMP_RE.finditer(raw_value))
        if not status_matches or not timestamp_matches:
            return

        for status_match in status_matches:
            word = status_match.group(1).lower()

            # Ignore statistic counters such as "0 defeated" or "4 defeated".
            if word in {"defeated", "slain", "killed"}:
                prefix = raw_value[max(0, status_match.start() - 16):status_match.end()]
                if re.search(r"\b\d+\s+" + re.escape(word) + r"\b", prefix, re.I):
                    continue

            outcome = "escaped" if word in {
                "expired", "escaped", "fled", "ran away"
            } else "defeated"

            for timestamp_match in timestamp_matches:
                timestamp = int(timestamp_match.group(1))
                distance = min(
                    abs(status_match.start() - timestamp_match.end()),
                    abs(timestamp_match.start() - status_match.end()),
                )
                candidates.append((distance, outcome, timestamp))

    # First scan individual values. This is the most precise case.
    for raw_value in _iter_string_values(data):
        scan(raw_value)

    # Components-v2 may split a label and its timestamp into neighboring text
    # displays. Scan the reconstructed visible text as a safe secondary path.
    scan(extract_all_text_from_raw(data))

    return candidates


def detect_boss_outcome(text: str) -> str | None:
    """Detect a clear boss result sentence while ignoring counters like `0 defeated`."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return None

    normalized = COUNTER_TEXT_RE.sub(" ", normalized)
    if any(pattern.search(normalized) for pattern in DEFEATED_PATTERNS):
        return "defeated"
    if any(pattern.search(normalized) for pattern in ESCAPED_PATTERNS):
        return "escaped"
    return None


def detect_boss_outcome_event(
    data: dict[str, Any],
    now: int | None = None,
) -> tuple[str, int] | None:
    """Return (outcome, actual outcome timestamp), or None when not trustworthy.

    Priority:
    1. OwO's visible Discord timestamp next to expired/escaped/defeated.
    2. A recent Discord edited/message timestamp for a clear result sentence.

    We deliberately do not use the current clock as the outcome time. Doing so
    would turn an old expired card into a brand-new five-minute cooldown whenever
    that old message is edited or interacted with.
    """
    now = int(time.time()) if now is None else int(now)

    candidates = _status_timestamp_candidates(data)
    if candidates:
        # The nearest timestamp to the outcome word is the authoritative one.
        _, outcome, event_time = min(candidates, key=lambda item: item[0])
        return outcome, event_time

    outcome = detect_boss_outcome(extract_all_text_from_raw(data))
    if outcome is None:
        return None

    for key in ("edited_timestamp", "timestamp"):
        event_time = _parse_iso_timestamp(data.get(key))
        if event_time is None:
            continue
        if 0 <= now - event_time <= RECENT_OUTCOME_FALLBACK_SECONDS:
            return outcome, event_time

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
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        print(f"[RAW FETCH] Could not read message {message_id}: {exc}")
        return None


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
    if weapon_type=="Bleeding Gaze": w_values=[wp_cost]+weapon_values
    elif weapon_type=="Orb of Potency": w_values=[]
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
        print(f"[BOSS PAGE] Could not read page position for {boss_title!r}; ignored")
        return

    parse_text = _build_parse_text(boss_title, description)
    signature = hashlib.sha1(parse_text.encode("utf-8", errors="ignore")).hexdigest()

    # Repeated gateway edit events and revisiting a page are common. Ignore the
    # page only when the exact same content is already stored in that slot.
    if session.page_signatures.get(page_number) == signature:
        return

    is_new_page = page_number not in session.page_texts
    session.page_texts[page_number] = parse_text
    session.hp_by_page[page_number] = extract_hp_from_embed(description)
    session.page_signatures[page_number] = signature

    action = "captured" if is_new_page else "updated"
    print(
        f"[BOSS PAGE] {action} page {page_number}/3: {boss_title} "
        f"({session.step}/3 unique pages)"
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
            print(f"[BOSS PAGE] Reaction failed: {exc}")

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
        self._restored = False

    def cog_unload(self) -> None:
        for task in self.cooldown_tasks.values():
            task.cancel()
        self.cooldown_tasks.clear()

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

        await interaction.followup.send(
            f"✅ Automatic boss cooldown alerts will be sent in {channel.mention}. "
            "Messages will not be pinned.",
            ephemeral=True,
        )

    @app_commands.command(
        name="boss-cooldown",
        description="Check whether the guild boss cooldown is active.",
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

        await interaction.response.send_message(
            embed=self.build_cooldown_embed(config),
            ephemeral=True,
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
        await self.restore_cooldowns()

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
                    print(f"[COOLDOWN] Could not unpin old status message: {exc}")
            if "message_id" in config:
                config.pop("message_id", None)
                changed = True

            if cooldown_end > now:
                self.schedule_ready_update(guild_id, cooldown_end)
            elif cooldown_end > 0:
                # The bot was offline when the timer ended. Mark it ready and
                # send the missed ready alert once after reconnecting.
                config["cooldown_end"] = 0
                config["last_result"] = "ready"
                changed = True
                try:
                    await self.send_ready_message(guild_id)
                except Exception as exc:
                    print(f"[COOLDOWN] Could not restore guild {guild_id}: {exc}")

        if changed:
            save_cooldown_config(self.cooldown_config)

    # ── Gateway listeners ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            # Human trigger: only exact owo-boss-inventory forms.
            if not message.author.bot:
                if message.guild and is_boss_trigger(message.content):
                    active_sessions[message.channel.id] = BossSession(
                        user_id=message.author.id,
                        channel_id=message.channel.id,
                    )
                    print(
                        f"[TRIGGER] {message.author} armed boss reader in "
                        f"#{getattr(message.channel, 'name', message.channel.id)}"
                    )
                return

            if message.author.id != OWO_BOT_ID or message.guild is None:
                return

            generator_needed = message.channel.id in active_sessions
            cooldown_needed = self.is_cooldown_configured(message.guild.id)
            if not generator_needed and not cooldown_needed:
                return

            data = await fetch_raw_message(self.bot, message.channel.id, message.id)
            if not data:
                return

            # Double-check the fetched author before trusting the payload.
            if int((data.get("author") or {}).get("id", 0)) != OWO_BOT_ID:
                return

            if cooldown_needed:
                await self.maybe_handle_outcome(message.guild.id, message.id, data)

            if generator_needed:
                boss_title, description = extract_boss_from_raw(data)
                if boss_title:
                    page_number = extract_boss_page_number(data)
                    await process_boss_page(
                        self,
                        message.channel.id,
                        message.id,
                        boss_title,
                        description,
                        page_number,
                    )

        except Exception as exc:
            import traceback
            print(f"[ERROR on_message] {exc}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        try:
            if payload.guild_id is None:
                return

            generator_needed = payload.channel_id in active_sessions
            cooldown_needed = self.is_cooldown_configured(payload.guild_id)
            if not generator_needed and not cooldown_needed:
                return

            data = await fetch_raw_message(self.bot, payload.channel_id, payload.message_id)
            if not data:
                return

            if int((data.get("author") or {}).get("id", 0)) != OWO_BOT_ID:
                return

            if cooldown_needed:
                await self.maybe_handle_outcome(payload.guild_id, payload.message_id, data)

            if generator_needed:
                boss_title, description = extract_boss_from_raw(data)
                if boss_title:
                    page_number = extract_boss_page_number(data)
                    await process_boss_page(
                        self,
                        payload.channel_id,
                        payload.message_id,
                        boss_title,
                        description,
                        page_number,
                    )

        except Exception as exc:
            import traceback
            print(f"[ERROR on_raw_message_edit] {exc}")
            traceback.print_exc()

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

        embed = discord.Embed(
            title="⚔️ Boss Command Ready",
            description=f"```\n{command}\n```",
            color=0x57F287,
        )
        if warnings:
            embed.add_field(
                name="Parser note",
                value="\n".join(f"• {warning}" for warning in warnings[:4]),
                inline=False,
            )
        await channel.send(embed=embed)

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

        now = int(time.time())
        outcome_event = detect_boss_outcome_event(data, now=now)
        if outcome_event is None:
            return

        outcome, outcome_time = outcome_event
        cooldown_end = outcome_time + BOSS_COOLDOWN_SECONDS
        config = self.cooldown_config.setdefault(str(guild_id), {})

        last_source_id = int(config.get("last_source_message_id") or 0)
        last_outcome_time = int(config.get("last_outcome_time") or 0)
        if last_source_id == source_message_id and last_outcome_time == outcome_time:
            self.processed_outcome_messages.add(message_key)
            return

        self.processed_outcome_messages.add(message_key)
        if len(self.processed_outcome_messages) > 2000:
            self.processed_outcome_messages.clear()
            self.processed_outcome_messages.add(message_key)

        # Record this exact source+timestamp pair so repeated edits do not retrigger
        # it, but do not let stale cards block a separate current boss outcome.
        config["last_source_message_id"] = source_message_id
        config["last_outcome_time"] = outcome_time

        # An old expired/defeated card must never create a fresh cooldown.
        if cooldown_end <= now:
            config["cooldown_end"] = 0
            save_cooldown_config(self.cooldown_config)
            print(
                f"[COOLDOWN] Guild {guild_id}: ignored stale {outcome} outcome "
                f"from {outcome_time}; it was ready at {cooldown_end}"
            )
            return

        # Guard against malformed/future timestamps that are not outcome times.
        if outcome_time > now + 60:
            save_cooldown_config(self.cooldown_config)
            print(
                f"[COOLDOWN] Guild {guild_id}: ignored future outcome timestamp "
                f"{outcome_time}"
            )
            return

        # A second Discord message can describe the same outcome. Deduplicate by
        # the actual event timestamp rather than by when our bot happened to see it.
        previous_active_outcome = int(config.get("active_outcome_time") or 0)
        if previous_active_outcome and abs(outcome_time - previous_active_outcome) < OUTCOME_DEDUP_SECONDS:
            save_cooldown_config(self.cooldown_config)
            return

        config["cooldown_end"] = cooldown_end
        config["last_result"] = outcome
        config["last_detected_at"] = now
        config["active_outcome_time"] = outcome_time
        config.pop("message_id", None)
        save_cooldown_config(self.cooldown_config)

        print(
            f"[COOLDOWN] Guild {guild_id}: boss {outcome} at {outcome_time}; "
            f"ready at {cooldown_end}"
        )
        await self.send_cooldown_started_message(guild_id)
        self.schedule_ready_update(guild_id, cooldown_end)

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
        result = str(config.get("last_result") or "ready")

        if cooldown_end > now:
            result_text = (
                "The guild boss was **defeated**."
                if result == "defeated"
                else "The guild boss **escaped**."
            )
            embed = discord.Embed(
                title="⏳ Guild Boss Cooldown",
                description=(
                    f"{result_text}\n\n"
                    f"**Next boss cooldown ends:** <t:{cooldown_end}:R>\n"
                    f"**Ready at:** <t:{cooldown_end}:F>"
                ),
                color=0xFEE75C,
            )
            embed.set_footer(
                text="Discord displays the time correctly for each member's timezone."
            )
            return embed

        return discord.Embed(
            title="✅ Guild Boss Ready",
            description="The 5-minute cooldown has ended. A new guild boss can appear.",
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
            print(
                "[COOLDOWN] Old status message could not be unpinned automatically: "
                f"{exc}"
            )

    async def send_cooldown_started_message(self, guild_id: int) -> None:
        config = self.cooldown_config.get(str(guild_id))
        if not config:
            return

        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            print(f"[COOLDOWN] Configured channel for guild {guild_id} is unavailable.")
            return

        try:
            await channel.send(embed=self.build_cooldown_embed(config))
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[COOLDOWN] Could not send cooldown alert: {exc}")

    async def send_ready_message(self, guild_id: int) -> None:
        channel = await self.get_configured_channel(guild_id)
        if channel is None:
            print(f"[COOLDOWN] Configured channel for guild {guild_id} is unavailable.")
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
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[COOLDOWN] Could not send ready alert: {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BossGenerator(bot))
