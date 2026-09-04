"""Focused gameplay emoji catalog; legacy uploaded icons remain usable."""

from functools import lru_cache
import json
from pathlib import Path

from .team_templates import STANDARD_ANIMAL_NAMES

SPECIAL_EMOJI_FILE = Path(__file__).resolve().parent.parent / "data" / "guide_special_emojis.json"
GAMEPLAY_RANKS = frozenset({"common", "uncommon", "rare", "epic", "mythical", "mythic",
                          "legendary", "gem", "fabled", "bot", "hidden", "distorted"})
PATREON_ANIMALS = frozenset({"pbird", "pdolphin", "pogre", "pscorpion", "ptiger"})
BASE_ANIMALS = (STANDARD_ANIMAL_NAMES - PATREON_ANIMALS) | {"glitchcrab", "glitchheart", "glitchshark"}

# Verified gameplay identities only. Do not infer arbitrary tier prefixes:
# custom Patreon pets can share names, and hsquid is NOT ordinary squid.
ANIMAL_DEX_ALIASES = {
    "pet_dboar": "pet_boar", "pet_gcamel": "pet_camel", "pet_gdeer": "pet_deer",
    "pet_deagle": "pet_eagle", "pet_gfish": "pet_fish", "pet_gfox": "pet_fox",
    "pet_dfrog": "pet_frog", "pet_dgorilla": "pet_gorilla", "pet_hkoala": "pet_koala",
    "pet_glion": "pet_lion", "pet_hlizard": "pet_lizard", "pet_hmonkey": "pet_monkey",
    "pet_gowl": "pet_owl", "pet_gpanda": "pet_panda", "pet_gshrimp": "pet_shrimp",
    "pet_hsnake": "pet_snake", "pet_gspider": "pet_spider", "pet_gsquid": "pet_squid",
    "pet_dwolf": "pet_wolf",
}


def canonical_emoji_key(key: str) -> str:
    return ANIMAL_DEX_ALIASES.get(key, key)


def emoji_key_group(key: str) -> tuple[str, ...]:
    canonical = canonical_emoji_key(key)
    return (canonical, *(alias for alias, target in ANIMAL_DEX_ALIASES.items() if target == canonical))


def effective_override(overrides, key):
    return next((overrides[candidate] for candidate in emoji_key_group(key) if candidate in overrides), None)


@lru_cache(maxsize=1)
def special_emoji_keys() -> frozenset[str]:
    return frozenset("pet_" + key for key in json.loads(SPECIAL_EMOJI_FILE.read_text(encoding="utf-8"))["animals"])


def is_catalog_emoji(key: str) -> bool:
    if key in {"rank_patreon", "rank_custom_patreon"}:
        return False
    if not key.startswith("pet_"):
        return True
    if key in special_emoji_keys():
        return True
    animal = key[4:]
    return animal in BASE_ANIMALS or key in ANIMAL_DEX_ALIASES or animal == "hsquid"


def eligible_dex_record(record) -> bool:
    key = "pet_" + record.animal_key
    return is_catalog_emoji(key) and (record.rank in GAMEPLAY_RANKS
                                    or (record.rank == "special" and key in special_emoji_keys()))
