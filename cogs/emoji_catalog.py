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
    return animal in BASE_ANIMALS or (len(animal) > 1 and animal[0] in "curemgldfbh"
                                     and animal[1:] in BASE_ANIMALS)


def eligible_dex_record(record) -> bool:
    key = "pet_" + record.animal_key
    return is_catalog_emoji(key) and (record.rank in GAMEPLAY_RANKS
                                    or (record.rank == "special" and key in special_emoji_keys()))
