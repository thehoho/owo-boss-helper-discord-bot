"""Stable OwO animal, weapon, passive, and rank references used by team guides."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPECIAL_CATALOG_FILE = PROJECT_ROOT / "data" / "special_animals.json"


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    name: str
    emoji_key: str
    aliases: tuple[str, ...]


def normalize_catalog_token(value: str) -> str:
    value = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", value or "")
    value = value.casefold().replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _entry(key: str, name: str, emoji_stem: str, *aliases: str, prefix: str) -> CatalogEntry:
    values = tuple(dict.fromkeys(filter(None, (key, name, *aliases))))
    return CatalogEntry(key, name, f"{prefix}_{emoji_stem}", values)


WEAPONS: tuple[CatalogEntry, ...] = (
    _entry("sword", "Great Sword", "sword", "greatsword", prefix="weapon"),
    _entry("hstaff", "Healing Staff", "hstaff", "heal staff", prefix="weapon"),
    _entry("bow", "Bow", "bow", prefix="weapon"),
    _entry("rune", "Rune of the Forgotten", "rune", "forgotten rune", prefix="weapon"),
    _entry("shield", "Defender's Aegis", "shield", "aegis", "defenders aegis", prefix="weapon"),
    _entry("orb", "Orb of Potency", "orb", "potency orb", prefix="weapon"),
    _entry("vstaff", "Vampiric Staff", "vstaff", "vamp staff", prefix="weapon"),
    _entry("pd", "Poison Dagger", "pd", "dagger", prefix="weapon"),
    _entry("wand", "Wand of Absorption", "wand", "absorption wand", prefix="weapon"),
    _entry("fstaff", "Flame Staff", "fstaff", "fire staff", prefix="weapon"),
    _entry("estaff", "Energy Staff", "estaff", prefix="weapon"),
    _entry("sstaff", "Spirit Staff", "sstaff", prefix="weapon"),
    _entry("scepter", "Arcane Scepter", "scepter", "ascept", prefix="weapon"),
    _entry("rstaff", "Resurrection Staff", "rstaff", "res staff", prefix="weapon"),
    _entry("axe", "Glacial Axe", "axe", prefix="weapon"),
    _entry("vban", "Vanguard's Banner", "vban", "banner", prefix="weapon"),
    _entry("sythe", "Culling Scythe", "sythe", "scythe", prefix="weapon"),
    _entry("crune", "Rune of Celebration", "crune", "celebration rune", prefix="weapon"),
    _entry("pstaff", "Staff of Purity", "pstaff", "purity staff", prefix="weapon"),
    _entry("lsy", "Leeching Scythe", "lsy", "leech scythe", prefix="weapon"),
    _entry("ffish", "Foul Fish", "ffish", "fishing rod", prefix="weapon"),
    _entry("lrune", "Rune of Luck", "lrune", "luck rune", prefix="weapon"),
    _entry("cstaff", "Staff of Corruption", "cstaff", "corruption staff", prefix="weapon"),
    _entry("soul", "Soul Tithe", "soul", prefix="weapon"),
    _entry("bhstaff", "Briar-Heart Staff", "bhstaff", "briar heart staff", prefix="weapon"),
    _entry("edge", "Arbiter's Edge", "edge", "aedge", prefix="weapon"),
    _entry("xbow", "Wounding Crossbow", "xbow", "crossbow", prefix="weapon"),
    _entry("bgaz", "Bleeding Gaze", "bgaz", "gaze", prefix="weapon"),
    _entry("claw", "Conduit Claw", "claw", "cclaw", prefix="weapon"),
)


PASSIVES: tuple[CatalogEntry, ...] = (
    _entry("str", "Strength", "str", prefix="passive"),
    _entry("mag", "Magic", "mag", prefix="passive"),
    _entry("hp", "Health Point", "hp", "health", prefix="passive"),
    _entry("wp", "Weapon Point", "wp", prefix="passive"),
    _entry("pr", "Physical Resistance", "pr", prefix="passive"),
    _entry("mr", "Magic Resistance", "mr", "magical resistance", prefix="passive"),
    _entry("ls", "Lifesteal", "ls", prefix="passive"),
    _entry("th", "Thorns", "th", prefix="passive"),
    _entry("mtap", "Mana Tap", "mtap", prefix="passive"),
    _entry("absv", "Absolve", "absv", prefix="passive"),
    _entry("sg", "Safeguard", "sg", prefix="passive"),
    _entry("crit", "Critical", "crit", prefix="passive"),
    _entry("dc", "Discharge", "dc", prefix="passive"),
    _entry("kk", "Kamikaze", "kk", prefix="passive"),
    _entry("hgen", "Regeneration", "hgen", "regen", prefix="passive"),
    _entry("wgen", "Energize", "wgen", prefix="passive"),
    _entry("sprout", "Sprout", "sprout", "sprt", prefix="passive"),
    _entry("enra", "Enrage", "enra", "enrage", prefix="passive"),
    _entry("sac", "Sacrifice", "sac", prefix="passive"),
    _entry("snail", "Snail", "snail", prefix="passive"),
    _entry("kno", "Knowledge", "kno", prefix="passive"),
    _entry("gslay", "Giant Slayer", "gslay", prefix="passive"),
    _entry("adapt", "Adaptation", "adapt", prefix="passive"),
    _entry("res", "Resonance", "res", prefix="passive"),
    _entry("swarm", "Living Hive", "swarm", prefix="passive"),
    _entry("lwolf", "Lone Wolf", "lwolf", prefix="passive"),
    _entry("ds", "Double Strike", "ds", prefix="passive"),
    _entry("fr", "Frost Armor", "fr", prefix="passive"),
)


RANKS: tuple[CatalogEntry, ...] = (
    _entry("common", "Common", "common", "c", prefix="rank"),
    _entry("uncommon", "Uncommon", "uncommon", "u", prefix="rank"),
    _entry("rare", "Rare", "rare", "r", prefix="rank"),
    _entry("epic", "Epic", "epic", "e", prefix="rank"),
    _entry("mythical", "Mythical", "mythical", "mythic", "m", prefix="rank"),
    _entry("patreon", "Patreon", "patreon", "p", prefix="rank"),
    _entry("custom_patreon", "Custom Patreon", "custom_patreon", "cpatreon", "cp", prefix="rank"),
    _entry("gem", "Gem", "gem", "g", prefix="rank"),
    _entry("legendary", "Legendary", "legendary", "l", prefix="rank"),
    _entry("fabled", "Fabled", "fabled", "f", prefix="rank"),
    _entry("bot", "Bot", "bot", "b", prefix="rank"),
    _entry("hidden", "Hidden", "hidden", "h", prefix="rank"),
    _entry("distorted", "Distorted", "distorted", "d", prefix="rank"),
    _entry("special", "Special", "special", "s", prefix="rank"),
)


def _index(entries: Iterable[CatalogEntry]) -> dict[str, CatalogEntry]:
    result: dict[str, CatalogEntry] = {}
    for entry in entries:
        for alias in entry.aliases:
            normalized = normalize_catalog_token(alias)
            if normalized:
                result[normalized] = entry
    return result


WEAPON_INDEX = _index(WEAPONS)
PASSIVE_INDEX = _index(PASSIVES)
RANK_INDEX = _index(RANKS)


def resolve_weapon(value: str) -> CatalogEntry | None:
    return WEAPON_INDEX.get(normalize_catalog_token(value))


def resolve_passive(value: str) -> CatalogEntry | None:
    return PASSIVE_INDEX.get(normalize_catalog_token(value))


def resolve_rank(value: str) -> CatalogEntry | None:
    return RANK_INDEX.get(normalize_catalog_token(value))


@lru_cache(maxsize=1)
def special_catalog_payload() -> dict[str, object]:
    if not SPECIAL_CATALOG_FILE.is_file():
        return {}
    return dict(json.loads(SPECIAL_CATALOG_FILE.read_text(encoding="utf-8")))


def special_animals() -> tuple[dict[str, object], ...]:
    payload = special_catalog_payload()
    return tuple(dict(item) for item in payload.get("animals", []))


def special_catalog_updated_at() -> int:
    retrieved_at = str(special_catalog_payload().get("retrieved_at", ""))
    try:
        return int(datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        return 0


@lru_cache(maxsize=1)
def animal_index() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for animal in special_animals():
        for alias in animal.get("aliases", []):
            normalized = normalize_catalog_token(str(alias))
            if normalized:
                result[normalized] = animal
        normalized_name = normalize_catalog_token(str(animal.get("name", "")))
        if normalized_name:
            result[normalized_name] = animal
    return result


def resolve_special_animal(value: str) -> dict[str, object] | None:
    return animal_index().get(normalize_catalog_token(value))
