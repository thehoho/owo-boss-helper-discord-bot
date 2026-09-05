"""Read authoritative guild-boss HP from OwO's public battle logs.

OwO's current v2 battle-log endpoint stores the actual payload with the
compress-json format. This module implements the small decoding surface we
need locally, without requiring Node.js or another runtime dependency.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any


BATTLE_LOG_UUID_RE = re.compile(
    r"owobot\.com/battle-log\?uuid="
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE62_VALUES = {character: index for index, character in enumerate(BASE62_ALPHABET)}
MAX_COMPRESSED_VALUES = 250_000


@dataclass(frozen=True)
class BattleLogHP:
    uuid: str
    timestamp_ms: int
    initial_hp: tuple[int, int, int]
    final_hp: tuple[int, int, int]
    enemy_names: tuple[str, str, str]


def extract_battle_log_uuids(text: str) -> list[str]:
    """Return unique public battle-log UUIDs in displayed order."""
    values: list[str] = []
    seen: set[str] = set()
    for match in BATTLE_LOG_UUID_RE.finditer(text or ""):
        value = match.group(1).lower()
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def replace_command_hp(command: str, hp_values: tuple[int, int, int]) -> str:
    """Replace exactly the three values following -hp in a Neon command."""
    replacement = " -hp " + " ".join(str(max(0, int(value))) for value in hp_values)
    updated, count = re.subn(
        r"\s-hp\s+\d+\s+\d+\s+\d+",
        replacement,
        command or "",
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("The saved boss command does not contain exactly three HP values.")
    return updated


def _base62_to_int(value: str) -> int:
    if not value:
        raise ValueError("Empty base62 value.")
    result = 0
    for character in value:
        try:
            digit = BASE62_VALUES[character]
        except KeyError as exc:
            raise ValueError(f"Invalid base62 character: {character!r}") from exc
        result = result * 62 + digit
    return result


def _encoded_integer_to_text(value: str) -> str:
    if value.startswith(":"):
        value = value[1:]
    return str(_base62_to_int(value))


def _decode_number(value: str) -> int | float:
    negative = value.startswith("-")
    if negative:
        value = value[1:]
    parts = value.split(".")
    if len(parts) == 1:
        result: int | float = _base62_to_int(parts[0])
    elif len(parts) in (2, 3):
        integer = _encoded_integer_to_text(parts[0])
        fraction = _encoded_integer_to_text(parts[1])[::-1]
        number_text = f"{integer}.{fraction}"
        if len(parts) == 3:
            exponent = parts[2]
            exponent_sign = ""
            if exponent.startswith("-"):
                exponent_sign = "-"
                exponent = exponent[1:]
            number_text += f"e{exponent_sign}{_encoded_integer_to_text(exponent)}"
        result = float(number_text)
    else:
        raise ValueError("Invalid compressed number.")
    return -result if negative else result


def decompress_json(payload: Any) -> Any:
    """Decode a value produced by the open-source compress-json package."""
    if (
        not isinstance(payload, list)
        or len(payload) != 2
        or not isinstance(payload[0], list)
    ):
        raise ValueError("Invalid compressed JSON envelope.")
    values, root = payload
    if len(values) > MAX_COMPRESSED_VALUES:
        raise ValueError("Compressed JSON dictionary is too large.")

    memo: dict[int, Any] = {}

    def decode_key(key: Any) -> Any:
        if key in ("", "_"):
            return None
        if isinstance(key, int):
            index = key
        elif isinstance(key, str):
            index = _base62_to_int(key)
        else:
            raise ValueError("Invalid compressed JSON key.")
        return decode(index)

    def decode(index: int) -> Any:
        if index < 0 or index >= len(values):
            raise ValueError("Compressed JSON key is out of range.")
        if index in memo:
            return memo[index]
        value = values[index]
        if value is None or isinstance(value, (int, float, bool)):
            memo[index] = value
            return value
        if not isinstance(value, str):
            raise ValueError("Unsupported compressed JSON value.")
        if len(value) < 2 or value[1] != "|":
            memo[index] = value
            return value

        kind = value[0]
        encoded = value[2:]
        if kind == "s":
            result: Any = encoded
        elif kind == "b":
            if encoded not in {"T", "F"}:
                raise ValueError("Invalid compressed boolean.")
            result = encoded == "T"
        elif kind == "n":
            result = _decode_number(encoded)
        elif kind == "N":
            try:
                result = {"+": math.inf, "-": -math.inf, "0": math.nan}[encoded]
            except KeyError as exc:
                raise ValueError("Invalid compressed special number.") from exc
        elif kind == "a":
            result = []
            memo[index] = result
            if encoded:
                result.extend(decode_key(key) for key in value.split("|")[1:])
        elif kind == "o":
            result = {}
            memo[index] = result
            if encoded:
                parts = value.split("|")
                keys = decode_key(parts[1])
                if len(parts) - 2 == 1 and not isinstance(keys, list):
                    keys = [keys]
                if not isinstance(keys, list) or len(keys) != len(parts) - 2:
                    raise ValueError("Invalid compressed object key template.")
                for offset, encoded_value in enumerate(parts[2:]):
                    key = keys[offset]
                    if not isinstance(key, (str, int, float, bool)):
                        raise ValueError("Invalid compressed object property.")
                    result[str(key)] = decode_key(encoded_value)
        else:
            raise ValueError(f"Unknown compressed JSON value type: {kind!r}")

        memo[index] = result
        return result

    return decode_key(root)


def _validated_team_hp(
    state: dict[str, Any],
    enemy_ids: list[str],
) -> tuple[int, int, int]:
    values: list[int] = []
    for enemy_id in enemy_ids:
        member = state.get(enemy_id)
        hp = member.get("hp") if isinstance(member, dict) else None
        if isinstance(hp, bool) or not isinstance(hp, (int, float)):
            raise ValueError("Battle log is missing an enemy HP value.")
        if not math.isfinite(hp) or hp < 0 or hp > 100_000_000 or int(hp) != hp:
            raise ValueError("Battle log contains an invalid enemy HP value.")
        values.append(int(hp))
    if len(values) != 3:
        raise ValueError("Battle log does not contain a three-member enemy team.")
    return values[0], values[1], values[2]


def extract_battle_log_hp(payload: dict[str, Any], uuid: str) -> BattleLogHP:
    """Extract initial/final boss HP in OwO's authoritative enemy-team order."""
    if not isinstance(payload, dict):
        raise ValueError("Battle-log response is not an object.")

    decoded: Any
    if payload.get("v2") is True:
        compressed = payload.get("logs")
        if isinstance(compressed, str):
            compressed = json.loads(compressed)
        decoded = decompress_json(compressed)
    else:
        decoded = payload

    if not isinstance(decoded, dict):
        raise ValueError("Decoded battle log is not an object.")
    metadata = decoded.get("metadata")
    info = metadata.get("info") if isinstance(metadata, dict) else None
    enemy = info.get("enemy") if isinstance(info, dict) else None
    enemy_ids = enemy.get("team") if isinstance(enemy, dict) else None
    battle = decoded.get("battle")
    if (
        not isinstance(enemy_ids, list)
        or len(enemy_ids) != 3
        or not all(isinstance(value, str) and value for value in enemy_ids)
        or not isinstance(battle, list)
        or not battle
    ):
        raise ValueError("Decoded battle log has no three-member enemy battle.")

    initial_state = battle[0].get("state") if isinstance(battle[0], dict) else None
    final_state = battle[-1].get("state") if isinstance(battle[-1], dict) else None
    if not isinstance(initial_state, dict) or not isinstance(final_state, dict):
        raise ValueError("Decoded battle log has no initial/final state.")

    timestamp = info.get("date")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError("Decoded battle log has no timestamp.")
    timestamp_ms = int(timestamp)
    if timestamp_ms <= 0:
        raise ValueError("Decoded battle log timestamp is invalid.")

    names: list[str] = []
    for enemy_id in enemy_ids:
        entry = metadata.get(enemy_id)
        name = entry.get("name") if isinstance(entry, dict) else None
        names.append(str(name or "Unknown"))

    return BattleLogHP(
        uuid=uuid.lower(),
        timestamp_ms=timestamp_ms,
        initial_hp=_validated_team_hp(initial_state, enemy_ids),
        final_hp=_validated_team_hp(final_state, enemy_ids),
        enemy_names=(names[0], names[1], names[2]),
    )
