"""
cogs/boss_generator.py â€” automatic OwO boss command generator and cooldown tracker.

Generator:
- Watches only exact OwO boss-inventory commands after whitespace is removed:
  `owobossi` and `wbossi` (so `owo boss i`, `owoboss i`, `w boss i`, etc. work).
- Reads the three paginated OwO boss cards, orders them by the visible 1/3â€“3/3 counter, and posts the Neon battle command.

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
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands

from .message_utils import safe_reply
from .owo_prefix import (
    OWO_PREFIX_DEFAULT,
    get_guild_owo_prefix,
    is_owo_prefixed_command,
    owo_command,
)
from .ui_emojis import ensure_ui_emojis, ui_emoji_text


logger = logging.getLogger(__name__)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONSTANTS & CONFIG
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

DEFAULT_HP = "80000"
LVL_RE = re.compile(r"Lvl\s+\d+", re.I)
PAGE_POSITION_RE = re.compile(r"^\s*([1-3])\s*/\s*3\s*$")
PAGE_POSITION_SEARCH_RE = re.compile(r"(?<!\d)([1-3])\s*/\s*3(?!\d)")

# Official verified OwO Bot user ID.
OWO_BOT_ID = 408785106942164992

# OwO command suffixes accepted after all whitespace is removed. The actual
# short prefix is read per server from the shared OwO prefix setting.
BOSS_TRIGGER_SUFFIXES = {"bossi"}

# Lightweight public helper commands. Whitespace and capitalization are ignored.
PREFIX_COOLDOWN_TRIGGERS = {"hbosscd", "hbosscooldown"}
PREFIX_HELP_TRIGGERS = {"hhelp"}
BOSS_DECISION_HIT_TRIGGERS = {"hbosshit", "hsethit"}
BOSS_DECISION_SKIP_TRIGGERS = {"hbossskip", "hskipboss", "hsetskip"}

SESSION_TIMEOUT_SECONDS = 180
BOSS_COOLDOWN_SECONDS = 5 * 60
OUTCOME_DEDUP_SECONDS = 20
OUTCOME_SETTLE_SECONDS = 1.25
BOSS_WATCH_INTERVAL_SECONDS = 15
PACIFIC = ZoneInfo("America/Los_Angeles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOLDOWN_CONFIG_FILE = PROJECT_ROOT / "boss_cooldown_config.json"
HP_TEMPLATE_DIR = PROJECT_ROOT / "assets" / "hp_digits"


def current_boss_report_cycle(now: datetime | None = None) -> str:
    current = (now or datetime.now(tz=PACIFIC)).astimezone(PACIFIC)
    return current.date().isoformat()


def boss_report_cycle_bounds(cycle: str) -> tuple[int, int]:
    day = date.fromisoformat(cycle)
    start = datetime.combine(day, datetime_time.min, tzinfo=PACIFIC)
    end = datetime.combine(day + timedelta(days=1), datetime_time.min, tzinfo=PACIFIC)
    return int(start.timestamp()), int(end.timestamp())


def next_boss_report_reset_timestamp(now: datetime | None = None) -> int:
    current = (now or datetime.now(tz=PACIFIC)).astimezone(PACIFIC)
    next_day = current.date() + timedelta(days=1)
    return boss_report_cycle_bounds(next_day.isoformat())[0]


def compact_first_line(content: str) -> str:
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    return re.sub(r"\s+", "", first_line).lower()


def parse_boss_decision_command(content: str) -> str | None:
    compact = compact_first_line(content)
    if compact in BOSS_DECISION_HIT_TRIGGERS:
        return "hit"
    if compact in BOSS_DECISION_SKIP_TRIGGERS:
        return "skip"
    return None


def parse_boss_sticky_command(content: str) -> str | None:
    compact = compact_first_line(content)
    if compact in {"hstickyclear", "hclearsticky"}:
        return "clear"
    if compact in {"hstickyoff"}:
        return "off"
    if compact in {"hstickyon"}:
        return "on"
    if compact == "hsticky":
        return "set"
    return None


def is_boss_trigger(content: str, owo_prefix: str = OWO_PREFIX_DEFAULT) -> bool:
    """Accept `owo boss i` or this server's configured short OwO prefix."""
    return is_owo_prefixed_command(content, owo_prefix, BOSS_TRIGGER_SUFFIXES)


def is_prefix_cooldown_trigger(content: str) -> bool:
    """Accept `H boss cd` and `H boss cooldown`, case-insensitively."""
    normalized = re.sub(r"\s+", "", content or "").lower()
    return normalized in PREFIX_COOLDOWN_TRIGGERS


def is_prefix_help_trigger(content: str) -> bool:
    """Accept `H help` at the start and ignore any following text."""
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    return re.match(r"^h\s*help\b", first_line, re.IGNORECASE) is not None


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
    "Bleeding Gaze":        {"values":[[30,50],[20,40],[150,200],[20,10]],             "alias":"bgaz"},
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
    "Critical":           ßİüÚÚ$z{-®éÜj×2–væ÷&RâöÆB&W7VÇBv†÷6Rf—fRÖÖ–çWFRFVfVB6ööÆF÷vâÇ&VG’VæFVBàĞ¢–b6ööÆF÷våöVæBÃÒæ÷s Ğ¢6öæf–u²&6ööÆF÷våöVæB%ÒÒ Ğ¢6öæf–u²&Æ7E÷&W7VÇB%ÒÒ'&VG’ Ğ¢6fUö6ööÆF÷våö6öæf–r‡6VÆbæ6ööÆF÷våö6öæf–rĞ¢ÆövvW"æ–æfò‚$–væ÷&VBöÆBFVfVFVB&W7VÇBg&öÒW2"ÂWfVçE÷F–ÖRĞ¢&WGW&àĞ Ğ¢ÆövvW"æ–æfò€¢$wV–ÆBW2&÷72FVfVFVC²6ööÆF÷vâVæG2BW2"À¢wV–ÆEö–BÀ¢6ööÆF÷våöVæBÀ¢¢v—B6VÆbç&V6÷&Eö&÷75÷&W÷'Eö÷WF6öÖR†wV–ÆEö–BÂ&FVfVFVB"¢v—B6VÆbæ6ÆV%ö&÷75öFV6—6–öåöÖW76vR†wV–ÆEö–B¢v—B6VÆbç6VæEö&÷75ö÷WF6öÖUöÖ&¶W"†wV–ÆEö–BÂ&FVfVFVB"Ğ¢v—B6VÆbç6VæEö6ööÆF÷vå÷7F'FVEöÖW76vR†wV–ÆEö–BĞ¢6VÆbç66†VGVÆU÷&VG•÷WFFR†wV–ÆEö–BÂ6ööÆF÷våöVæBĞ Ğ¢7–æ2FVbf–æ—6…ö&÷75öW66R€Ğ¢6VÆbÀĞ¢wV–ÆEö–C¢–çBÀĞ¢6÷W&6UöÖW76vUö–C¢–çBÀĞ¢WfVçE÷F–ÖS¢–çBÀĞ¢¢ÀĞ¢&÷75ö¶W“¢–çBÂæöæRÒæöæRÀĞ¢’ÓâæöæS Ğ¢""$Ö&²F†RwV–ÆB&VG’–ÖÖVF–FVÇ’&V6W6RW66W2†fRæò6ööÆF÷vââ"" Ğ¢2v—B'&–VfÇ’f÷"÷tòw2VF—B'W'7BFò6WGFÆRÂF†VâÆWBW†7FÇ’öæRF6°Ğ¢26Æ–ÒæBææ÷Væ6RF†—2&÷72÷WF6öÖRàĞ¢v—B7–æ6–òç6ÆVW„õUD4ôÔUõ4UEDÄUõ4T4ôäE2Ğ¢7–æ2v—F‚6VÆbævWEöwV–ÆEö&÷75ö÷WF6öÖUöÆö6²†wV–ÆEö–B“ Ğ¢VffV7F—fUö&÷75ö¶W’Ò–çB†&÷75ö¶W’÷"’÷"WfVçE÷F–ÖPĞ¢6Æ–ÖVBÒ6VÆbæ6Æ–Õö&÷75ö÷WF6öÖR€Ğ¢wV–ÆEö–BÀĞ¢6÷W&6UöÖW76vUö–BÀĞ¢&W66VB"ÀĞ¢WfVçE÷F–ÖRÀĞ¢VffV7F—fUö&÷75ö¶W’ÀĞ¢Ğ¢–b6Æ–ÖVB—2æöæS Ğ¢&WGW&àĞ Ğ¢6öæf–rÂæ÷rÒ6Æ–ÖV@Ğ¢6öæf–u²&6ööÆF÷våöVæB%ÒÒ Ğ Ğ¢öÆE÷F6²Ò6VÆbæ6ööÆF÷vå÷F6·2ç÷†wV–ÆEö–BÂæöæRĞ¢–böÆE÷F6³ Ğ¢öÆE÷F6²æ6æ6VÂ‚Ğ Ğ¢6fUö6ööÆF÷våö6öæf–r‡6VÆbæ6ööÆF÷våö6öæf–rĞ Ğ¢2Fòæ÷BV&Æ—6‚g&W6‚ÆW'Bf÷"fW'’öÆBW66VB6&BVæ6÷VçFW&V@Ğ¢2GW&–ær†—7F÷'’&W7F÷&F–öâ÷"gFW"ÆöæröffÆ–æRW&–öBàĞ¢–bæ÷rÒWfVçE÷F–ÖRâ$õ55ô4ôôÄDõtåõ4T4ôäE3 Ğ¢ÆövvW"æ–æfò‚$–væ÷&VBöÆBW66VB&W7VÇBg&öÒW2"ÂWfVçE÷F–ÖRĞ¢&WGW&àĞ Ğ¢ÆövvW"æ–æfò€¢$wV–ÆBW2&÷72W66VBBW3²æò6ööÆF÷vâÆ–W2"À¢wV–ÆEö–BÀ¢WfVçE÷F–ÖRÀ¢¢v—B6VÆbç&V6÷&Eö&÷75÷&W÷'Eö÷WF6öÖR†wV–ÆEö–BÂ&W66VB"¢v—B6VÆbæ6ÆV%ö&÷75öFV6—6–öåöÖW76vR†wV–ÆEö–B¢v—B6VÆbç6VæEö&÷75ö÷WF6öÖUöÖ&¶W"†wV–ÆEö–BÂ&W66VB"Ğ¢v—B6VÆbç6VæEöW66U÷&VG•öÖW76vR†wV–ÆEö–BĞ Ğ¢FVb66†VGVÆU÷&VG•÷WFFR‡6VÆbÂwV–ÆEö–C¢–çBÂ6ööÆF÷våöVæC¢–çB’ÓâæöæS Ğ¢öÆE÷F6²Ò6VÆbæ6ööÆF÷vå÷F6·2ç÷†wV–ÆEö–BÂæöæRĞ¢–böÆE÷F6³ Ğ¢öÆE÷F6²æ6æ6VÂ‚Ğ¢6VÆbæ6ööÆF÷vå÷F6·5¶wV–ÆEö–EÒÒ7–æ6–òæ7&VFU÷F6²€Ğ¢6VÆbæf–æ—6…ö6ööÆF÷vå÷v†Vå÷&VG’†wV–ÆEö–BÂ6ööÆF÷våöVæBĞ¢Ğ Ğ¢7–æ2FVbf–æ—6…ö6ööÆF÷vå÷v†Vå÷&VG’‡6VÆbÂwV–ÆEö–C¢–çBÂW‡V7FVEöVæC¢–çB’ÓâæöæS Ğ¢G'“ Ğ¢v—B7–æ6–òç6ÆVW†Ö‚ƒÂW‡V7FVEöVæBÒF–ÖRçF–ÖR‚’’Ğ¢6öæf–rÒ6VÆbæ6ööÆF÷våö6öæf–rævWB‡7G"†wV–ÆEö–B’Â·ÒĞ¢–b–çB†6öæf–rævWB‚&6ööÆF÷våöVæB"’÷"’ÒW‡V7FVEöVæC Ğ¢&WGW&àĞ Ğ¢6öæf–u²&6ööÆF÷våöVæB%ÒÒ Ğ¢6öæf–u²&Æ7E÷&W7VÇB%ÒÒ'&VG’ Ğ¢6fUö6ööÆF÷våö6öæf–r‡6VÆbæ6ööÆF÷våö6öæf–rĞ¢v—B6VÆbç6VæE÷&VG•öÖW76vR†wV–ÆEö–BĞ¢W†6WB7–æ6–òä6æ6VÆÆVDW'&÷# Ğ¢&WGW&àĞ¢f–æÆÇ“ Ğ¢7W'&VçBÒ6VÆbæ6ööÆF÷vå÷F6·2ævWB†wV–ÆEö–BĞ¢–b7W'&VçB—27–æ6–òæ7W'&VçE÷F6²‚“ Ğ¢6VÆbæ6ööÆF÷vå÷F6·2ç÷†wV–ÆEö–BÂæöæRĞ Ğ¢FVb'V–ÆEö6ööÆF÷våöVÖ&VB‡6VÆbÂ6öæf–s¢F–7E·7G"Âç•Ò’ÓâF—66÷&BäVÖ&VC Ğ¢æ÷rÒ–çB‡F–ÖRçF–ÖR‚’Ğ¢6ööÆF÷våöVæBÒ–çB†6öæf–rævWB‚&6ööÆF÷våöVæB"’÷"Ğ¢7F—fUöW‡—'’Ò–çB†6öæf–rævWB‚&7F—fUö&÷75öW‡—&W5öB"’÷"Ğ¢&W7VÇBÒ7G"†6öæf–rævWB‚&Æ7E÷&W7VÇB"’÷"'&VG’"Ğ¢VçfW&–f–VBÒ&ööÂ†6öæf–rævWB‚&7F—fUö&÷75÷VçfW&–f–VB"’Ğ Ğ¢–b6ööÆF÷våöVæBâæ÷s Ğ¢VÖ&VBÒF—66÷&BäVÖ&VB€Ğ¢F—FÆSÖb'·6VÆbçV•öVÖö¦’‚v&÷75öFVfVFVBrÂ~(û2r—ÒwV–ÆB&÷726ööÆF÷vâ"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†RwV–ÆB&÷72v2¢¦FVfVFVB¢¢åÆåÆâ Ğ¢b"¢¤æW‡B&÷726ööÆF÷vâVæG3¢¢¢ÇC§¶6ööÆF÷våöVæGÓ¥#åÆâ Ğ¢b"¢¥&VG’C¢¢¢ÇC§¶6ööÆF÷våöVæGÓ¤câ Ğ¢’ÀĞ¢6öÆ÷#Ó„dTSsT2ÀĞ¢Ğ¢VÖ&VBç6WEöfö÷FW"€Ğ¢FW‡CÒ$F—66÷&BF—7Æ—2F†RF–ÖR6÷'&V7FÇ’f÷"V6‚ÖVÖ&W"w2F–ÖW¦öæRâ Ğ¢Ğ¢&WGW&âVÖ&V@Ğ Ğ¢–b6öæf–rævWB‚&7F—fUö&÷75öÖW76vUö–B"’æBVçfW&–f–VC Ğ¢–b7F—fUöW‡—'’âæ÷s Ğ¢F–Ö–ærÒ€Ğ¢b%ÆåÆâ¢¤Æ7B¶æ÷vâW66RF–ÖS¢¢¢ÇC§¶7F—fUöW‡—'—Ó¥#åÆâ Ğ¢b"¢¤W†7BF–ÖS¢¢¢ÇC§¶7F—fUöW‡—'—Ó¤câ Ğ¢Ğ¢VÇ6S Ğ¢F–Ö–ærÒ" Ğ¢&WGW&âF—66÷&BäVÖ&VB€Ğ¢F—FÆSÒ.)ÙBwV–ÆB&÷727FGW2Væ6öæf—&ÖVB"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†RÆ7BG&6¶VB÷tò&÷72ÖW76vR—2æòÆöævW"f–Æ&ÆRÂ6òF†R Ğ¢&†VÇW"v–ÆÂæ÷B6Æ–ÒF†BF†R&÷72—27F–ÆÂ7F—fRâ'Vââ÷tò Ğ¢&&÷72×7FGW26öÖÖæB÷"6öçF–çVRw&–æF–æs²F†RæW‡B7FGW26&Bv–ÆÂ Ğ¢b'&Vg&W6‚F†—2WFöÖF–6ÆÇ’ç·F–Ö–æwÒ Ğ¢’ÀĞ¢6öÆ÷#Ó„dTSsT2ÀĞ¢Ğ Ğ¢–b6öæf–rævWB‚&7F—fUö&÷75öÖW76vUö–B"“ Ğ¢–b7F—fUöW‡—'’âæ÷s Ğ¢FW67&—F–öâÒ€Ğ¢$wV–ÆB&÷72—27W'&VçFÇ’7F—fRåÆåÆâ Ğ¢b"¢¥F†R&÷72v–ÆÂW66S¢¢¢ÇC§¶7F—fUöW‡—'—Ó¥#åÆâ Ğ¢b"¢¤W66RF–ÖS¢¢¢ÇC§¶7F—fUöW‡—'—Ó¤cåÆåÆâ Ğ¢$–bF†R&÷72—2FVfVFVBf—'7BÂF†Rf—fRÖÖ–çWFR6ööÆF÷vâ7F'G2 Ğ¢&g&öÒF†RFVfVBF–ÖRâ Ğ¢Ğ¢VÇ6S Ğ¢FW67&—F–öâÒ€Ğ¢$wV–ÆB&÷727FGW2—2&V–ærG&6¶VBÂ'WB—G2W†7BW66RF–ÖR Ğ¢&—2æ÷Bf–Æ&ÆR–WBâF†R†VÇW"v–ÆÂWFFRv†Vâ÷tòV&Æ—6†W2 Ğ¢&6ö×ÆWFR7FGW26&Bâ Ğ¢Ğ¢&WGW&âF—66÷&BäVÖ&VB€Ğ¢F—FÆSÖb'·6VÆbçV•öVÖö¦’‚v&÷75öV&VBrÂ~)©Nûˆòr—ÒwV–ÆB&÷727F—fR"ÀĞ¢FW67&—F–öãÖFW67&—F–öâÀĞ¢6öÆ÷#ÓƒSƒcTc"ÀĞ¢Ğ Ğ¢–b&W7VÇBÓÒ&W66VB# Ğ¢&WGW&âF—66÷&BäVÖ&VB€Ğ¢F—FÆSÖb'·6VÆbçV•öVÖö¦’‚v&÷75öW66VBrÂ~)ÈRr—Òæò7F—fRwV–ÆB&÷72"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†R&Wf–÷W2wV–ÆB&÷72¢¦W66VB¢¢âF†W&R—2æò6ööÆF÷vâgFW"â Ğ¢&W66RÂæBæòæWr&÷72†2&VVâFWFV7FVB–WBâ¶VWw&–æF–ærFò Ğ¢'7vâF†RæW‡BwV–ÆB&÷72â Ğ¢’ÀĞ¢6öÆ÷#ÓƒStc#ƒrÀĞ¢Ğ Ğ¢–b&W7VÇB–â²&FVfVFVB"Â'&VG’'Ó Ğ¢&WGW&âF—66÷&BäVÖ&VB€Ğ¢F—FÆSÒ.)ÈRwV–ÆB&÷72&VG’"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†W&R—27W'&VçFÇ’æò6öæf—&ÖVBwV–ÆB&÷72÷"6ööÆF÷vââF†R&Wf–÷W2 Ğ¢&FVfVB6ööÆF÷vâ†2VæFVBÂ6ò¶VWw&–æF–ærFò7vâæWr&÷72â Ğ¢’ÀĞ¢6öÆ÷#ÓƒStc#ƒrÀĞ¢Ğ Ğ¢&WGW&âF—66÷&BäVÖ&VB€Ğ¢F—FÆSÒ.)ÈRæò7F—fRwV–ÆB&÷72"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†W&R—27W'&VçFÇ’æò6öæf—&ÖVBwV–ÆB&÷72÷"6ööÆF÷vââ¶VWw&–æF–ær Ğ¢'Fò7vâæWrwV–ÆB&÷72â Ğ¢’ÀĞ¢6öÆ÷#ÓƒStc#ƒrÀĞ¢Ğ Ğ¢7–æ2FVbvWEö6öæf–wW&VEö6†ææVÂ‡6VÆbÂwV–ÆEö–C¢–çB’ÓâF—66÷&BåFW‡D6†ææVÂÂæöæS Ğ¢6öæf–rÒ6VÆbæ6ööÆF÷våö6öæf–rævWB‡7G"†wV–ÆEö–B’Â·ÒĞ¢6†ææVÅö–BÒ–çB†6öæf–rævWB‚&6†ææVÅö–B"’÷"Ğ¢–bæ÷B6†ææVÅö–C Ğ¢&WGW&âæöæPĞ Ğ¢6†ææVÂÒ6VÆbæ&÷BævWEö6†ææVÂ†6†ææVÅö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢G'“ Ğ¢6†ææVÂÒv—B6VÆbæ&÷BæfWF6…ö6†ææVÂ†6†ææVÅö–BĞ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bäæ÷Df÷VæBÂF—66÷&Bä…EEW†6WF–öâ“ Ğ¢&WGW&âæöæPĞ Ğ¢&WGW&â6†ææVÂ–b—6–ç7Fæ6R†6†ææVÂÂF—66÷&BåFW‡D6†ææVÂ’VÇ6RæöæPĞ Ğ¢7–æ2FVbVç–åöÆVv7•÷7FGW5öÖW76vR€Ğ¢6VÆbÂwV–ÆEö–C¢–çBÂÖW76vUö–C¢–ç@Ğ¢’ÓâæöæS Ğ¢""%Vç–âF†R7FGW2ÖW76vR7&VFVB'’F†R&Wf–÷W2&÷BfW'6–öââ"" Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢&WGW&àĞ Ğ¢G'“ Ğ¢ÖW76vRÒv—B6†ææVÂæfWF6…öÖW76vR†ÖW76vUö–BĞ¢–bÖW76vRç–ææVC Ğ¢v—BÖW76vRçVç–â‡&V6öãÒ$÷tò6ööÆF÷vâG&6¶W"æòÆöævW"W6W2–ææVBÖW76vW2"Ğ¢W†6WBF—66÷&Bäæ÷Df÷VæC Ğ¢&WGW&àĞ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$öÆB7FGW2ÖW76vR6÷VÆBæ÷B&RVç–ææVBWFöÖF–6ÆÇ“¢W2"ÂW†2Ğ Ğ Ğ¢7–æ2FVb6VæEö&÷75ö÷WF6öÖUöÖ&¶W"‡6VÆbÂwV–ÆEö–C¢–çBÂ÷WF6öÖS¢7G"’ÓâæöæS Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢&WGW&àĞ¢Ö&¶W"Ò6VÆbçV•öVÖö¦’‚&&÷75öFVfVFVB"Â$„•B"’–b÷WF6öÖRÓÒ&FVfVFVB"VÇ6R6VÆbçV•öVÖö¦’‚&&÷75öW66VB"Â%4´•"Ğ¢G'“ Ğ¢v—B6†ææVÂç6VæB‡7G"†Ö&¶W"’Ğ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$6÷VÆBæ÷B6VæB&÷72÷WF6öÖRÖ&¶W#¢W2"ÂW†2Ğ Ğ¢7–æ2FVb6VæEö6ööÆF÷vå÷7F'FVEöÖW76vR‡6VÆbÂwV–ÆEö–C¢–çB’ÓâæöæS Ğ¢6öæf–rÒ6VÆbæ6ööÆF÷våö6öæf–rævWB‡7G"†wV–ÆEö–B’Ğ¢–bæ÷B6öæf–s Ğ¢&WGW&àĞ Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢ÆövvW"çv&æ–ær‚$6öæf–wW&VB6ööÆF÷vâ6†ææVÂf÷"wV–ÆBW2—2Væf–Æ&ÆR"ÂwV–ÆEö–BĞ¢&WGW&àĞ Ğ¢G'“ Ğ¢v—B6†ææVÂç6VæB†VÖ&VC×6VÆbæ'V–ÆEö6ööÆF÷våöVÖ&VB†6öæf–r’Ğ¢ÆövvW"æ–æfò‚%6VçB6ööÆF÷vâÆW'Bf÷"wV–ÆBW2"ÂwV–ÆEö–BĞ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$6÷VÆBæ÷B6VæB6ööÆF÷vâÆW'C¢W2"ÂW†2Ğ Ğ¢7–æ2FVb6VæEöW66U÷&VG•öÖW76vR‡6VÆbÂwV–ÆEö–C¢–çB’ÓâæöæS Ğ¢""$ææ÷Væ6RF†BâW66VB&÷726â&R&WÆ6VB–ÖÖVF–FVÇ’â"" Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢ÆövvW"çv&æ–ær€Ğ¢$6öæf–wW&VB6ööÆF÷vâ6†ææVÂf÷"wV–ÆBW2—2Væf–Æ&ÆR"ÂwV–ÆEö–@Ğ¢Ğ¢&WGW&àĞ Ğ¢G'“ Ğ¢v—B6†ææVÂç6VæB€Ğ¢VÖ&VCÖF—66÷&BäVÖ&VB€Ğ¢F—FÆSÖb'·6VÆbçV•öVÖö¦’‚v&÷75öW66VBrÂu4´•r—ÒwV–ÆB&÷72W66VB"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†RwV–ÆB&÷72W66VBâF†W&R—2¢¦æò6ööÆF÷vâgFW"â Ğ¢&W66R¢¢Â6òæWrwV–ÆB&÷726âV"–ÖÖVF–FVÇ’â Ğ¢’ÀĞ¢6öÆ÷#ÓƒStc#ƒrÀĞ¢Ğ¢Ğ¢ÆövvW"æ–æfò‚%6VçB&÷72ÖW66VB&VG’ÆW'Bf÷"wV–ÆBW2"ÂwV–ÆEö–BĞ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$6÷VÆBæ÷B6VæB&÷72ÖW66VBÆW'C¢W2"ÂW†2Ğ Ğ¢7–æ2FVb6VæEöæWuö&÷75öÖW76vR‡6VÆbÂwV–ÆEö–C¢–çBÂW‡—'“¢–çBÂæöæR’ÓâæöæS Ğ¢""$ææ÷Væ6RæWvÇ’FWFV7FVBwV–ÆB&÷72öæ6R–âF†R6öæf–wW&VB6†ææVÂâ"" Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢ÆövvW"çv&æ–ær‚$6öæf–wW&VB6ööÆF÷vâ6†ææVÂf÷"wV–ÆBW2—2Væf–Æ&ÆR"ÂwV–ÆEö–BĞ¢&WGW&àĞ Ğ¢÷võ÷&Vf—‚Òv—BvWEöwV–ÆEö÷võ÷&Vf—‚†wV–ÆEö–B¢FV6—6–öå÷&öÆUö–G2Ò6VÆbæFV6—6–öå÷&öÆUö–G2†wV–ÆEö–B¢wV–ÆBÒ6VÆbæ&÷BævWEöwV–ÆB†wV–ÆEö–B¢7F—fUöÖVçF–öç2Ò€¢6VÆbæ7F—fUöÖVÖ&W%öÖVçF–öç5öf÷%÷&öÆW2†wV–ÆBÂFV6—6–öå÷&öÆUö–G2¢–bwV–ÆB—2æ÷BæöæP¢VÇ6RµĞ¢¢ÖVçF–öåöÆ–æW2Ò6VÆbæ6‡VæµöÖVçF–öç2†7F—fUöÖVçF–öç2¢V&VBÒ6VÆbçV•öVÖö¦’‚&&÷75öV&VB"Â.)©Nûˆò" ¢FW67&—F–öâÒ€¢b%W6R÷vò&÷72–÷"¶÷võö6öÖÖæB†÷võ÷&Vf—‚Âv&÷72’r—ÖFòÆWBF†R†VÇW" ¢'&VBF†RF‡&VR&÷72vW2åÆåÆâ ¢$&÷72†VÇW'26âW6R‚&÷72†—FÂ‚&÷726¶—Â÷"&WÇ’Fòæ÷FRv—F‚ ¢&‚7F–6·–åÆâ ¢%W6R‚7F–6·’6ÆV&Fò&VÖ÷fRF†R7W'&VçB7F–6·’âW6R‚†VÇf÷" ¢&6öæf–wW&F–öâ6öÖÖæG2â ¢¢–bW‡—'“ ¢FW67&—F–öâ³Ò€¢b%ÆåÆâ¢¤&÷72W66W3¢¢¢ÇC§¶W‡—'—Ó¥#åÆâ ¢b"¢¤W†7BF–ÖS¢¢¢ÇC§¶W‡—'—Ó¤câ ¢ ¢G'“ ¢v—B6†ææVÂç6VæB€¢VÖ&VCÖF—66÷&BäVÖ&VB€¢F—FÆSÖb'¶V&VGÒæWrwV–ÆB&÷72V&VB"À¢FW67&—F–öãÖFW67&—F–öâÀ¢6öÆ÷#ÓƒSƒcTc"À¢’À¢¢f÷"ÖVçF–öåöÆ–æR–âÖVçF–öåöÆ–æW3 ¢v—B6†ææVÂç6VæB€¢6öçFVçCÖÖVçF–öåöÆ–æRÀ¢ÆÆ÷vVEöÖVçF–öç3ÖF—66÷&BäÆÆ÷vVDÖVçF–öç2€¢&öÆW3ÔfÇ6RÀ¢W6W'3ÕG'VRÀ¢WfW'–öæSÔfÇ6RÀ¢’À¢¢ÆövvW"æ–æfò‚$ææ÷Væ6VBæWrwV–ÆB&÷72–âwV–ÆBW2"ÂwV–ÆEö–B¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$6÷VÆBæ÷B6VæBæWrÖ&÷72ÆW'C¢W2"ÂW†2Ğ Ğ¢7–æ2FVb6VæE÷&VG•öÖW76vR‡6VÆbÂwV–ÆEö–C¢–çB’ÓâæöæS Ğ¢6öæf–rÒ6VÆbæ6ööÆF÷våö6öæf–rævWB‡7G"†wV–ÆEö–B’Â·ÒĞ¢–b6öæf–rævWB‚&7F—fUö&÷75öÖW76vUö–B"“ Ğ¢2æWr&÷72—2Ç&VG’'Vææ–ærÂ6òvVæW&–2'&VG’Fò7vâ"ÆW'@Ğ¢2v÷VÆB&RÖ—6ÆVF–æràĞ¢&WGW&àĞ Ğ¢6†ææVÂÒv—B6VÆbævWEö6öæf–wW&VEö6†ææVÂ†wV–ÆEö–BĞ¢–b6†ææVÂ—2æöæS Ğ¢ÆövvW"çv&æ–ær‚$6öæf–wW&VB6ööÆF÷vâ6†ææVÂf÷"wV–ÆBW2—2Væf–Æ&ÆR"ÂwV–ÆEö–BĞ¢&WGW&àĞ Ğ¢G'“ Ğ¢v—B6†ææVÂç6VæB€Ğ¢VÖ&VCÖF—66÷&BäVÖ&VB€Ğ¢F—FÆSÖb'·6VÆbçV•öVÖö¦’‚v&÷75öFVfVFVBrÂ~)ÈRr—ÒwV–ÆB&÷72&VG’"ÀĞ¢FW67&—F–öãÒ€Ğ¢%F†RRÖÖ–çWFR6ööÆF÷vâ†2VæFVBâ Ğ¢$æWrwV–ÆB&÷726âæ÷rV"â Ğ¢’ÀĞ¢6öÆ÷#ÓƒStc#ƒrÀĞ¢Ğ¢Ğ¢ÆövvW"æ–æfò‚%6VçB&÷72×&VG’ÆW'Bf÷"wV–ÆBW2"ÂwV–ÆEö–BĞ¢W†6WB†F—66÷&Bäf÷&&–FFVâÂF—66÷&Bä…EEW†6WF–öâ’2W†3 Ğ¢ÆövvW"çv&æ–ær‚$6÷VÆBæ÷B6VæB&VG’ÆW'C¢W2"ÂW†2Ğ Ğ Ğ¦7–æ2FVb6WGW†&÷C¢6öÖÖæG2ä&÷B’ÓâæöæS Ğ¢v—B&÷BæFEö6ör„&÷74vVæW&F÷"†&÷B’Ğ