from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.boss_generator import BossGenerator
from cogs.emoji_assets import emoji_label
from cogs.emoji_tools import reference_entries
from cogs.game_catalog import EFFECTS, resolve_effect
from cogs.team_guides import (
    GuideBrowserView,
    GuideDraft,
    TeamGuideStore,
    TeamGuides,
    guide_variable_emoji_key,
)
from cogs.ui_emojis import emoji_asset_keys


class StickyMirrorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_cog(*, configured_channel_id: int = 10, target_channel_id: int = 20):
        command_message = SimpleNamespace(id=900)
        sticky_message = SimpleNamespace(id=901)
        partial = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(
            id=target_channel_id,
            send=AsyncMock(side_effect=[command_message, sticky_message]),
            get_partial_message=lambda _message_id: partial,
        )
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = SimpleNamespace(
            get_channel=lambda channel_id: channel if channel_id == target_channel_id else None,
            fetch_channel=AsyncMock(return_value=channel),
        )
        cog.cooldown_config = {
            "1": {
                "channel_id": configured_channel_id,
                "active_boss_message_id": 700,
                "last_result": "active",
                "decision_message_id": 701,
                "boss_decision": "skip",
                "boss_skip_emoji": "🐸",
                "generated_boss_replies": {},
            }
        }
        return cog, channel, partial

    async def test_off_channel_status_gets_one_independent_sticky_copy(self) -> None:
        cog, channel, partial = self.make_cog()
        command = "neon b myself vs bosses -hp 100000 100000 100000 -m"
        with patch("cogs.boss_generator.save_cooldown_config"):
            await cog.upsert_generated_boss_reply(1, 20, 800, command)
            await cog.upsert_generated_boss_reply(1, 20, 800, command)

        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(channel.send.await_args_list[0].args[0], f"`{command}`")
        self.assertEqual(channel.send.await_args_list[1].args[0], "# SKIP 🐸")
        entry = cog.cooldown_config["1"]["generated_boss_replies"]["800"]
        self.assertTrue(entry["sticky_mirror_checked"])
        self.assertEqual(entry["sticky_mirror_id"], 901)

        exact = command.replace("100000 100000 100000", "90000 80000 70000")
        with patch("cogs.boss_generator.save_cooldown_config"):
            await cog.upsert_generated_boss_reply(1, 20, 800, exact)
        partial.edit.assert_awaited_once_with(content=f"`{exact}`")
        self.assertEqual(channel.send.await_count, 2)

    async def test_configured_channel_keeps_only_its_persistent_sticky(self) -> None:
        cog, channel, _partial = self.make_cog(
            configured_channel_id=20,
            target_channel_id=20,
        )
        command = "neon b myself vs bosses -hp 1 2 3 -m"
        with patch("cogs.boss_generator.save_cooldown_config"):
            await cog.upsert_generated_boss_reply(1, 20, 800, command)
        self.assertEqual(channel.send.await_count, 1)
        self.assertIsNone(cog.boss_sticky_mirror_content(1, 20))

    def test_disabled_inactive_or_missing_stickies_are_not_mirrored(self) -> None:
        cog, _channel, _partial = self.make_cog()
        config = cog.cooldown_config["1"]
        config["decision_sticky_disabled"] = True
        self.assertIsNone(cog.boss_sticky_mirror_content(1, 20))
        config.pop("decision_sticky_disabled")
        config["last_result"] = "defeated"
        self.assertIsNone(cog.boss_sticky_mirror_content(1, 20))
        config["last_result"] = "active"
        config.pop("decision_message_id")
        self.assertIsNone(cog.boss_sticky_mirror_content(1, 20))


class EffectEmojiTests(unittest.TestCase):
    def test_all_published_effects_are_portable_guide_emojis(self) -> None:
        expected = {
            "attack_up",
            "attack_up_plus",
            "attack_up_plus_plus",
            "celebration",
            "defense_up",
            "flame",
            "freeze",
            "leech",
            "mortality",
            "poison",
            "stinky",
            "taunt",
        }
        self.assertTrue(expected.issubset({entry.key for entry in EFFECTS}))
        self.assertTrue(
            {f"effect_{key}" for key in expected}.issubset(emoji_asset_keys())
        )
        self.assertEqual(emoji_label("effect_attack_up_plus"), "EF_attack_up_plus")
        self.assertEqual(resolve_effect("atkupp").emoji_key, "effect_attack_up_plus")
        self.assertEqual(resolve_effect("defup").emoji_key, "effect_defense_up")
        self.assertEqual(guide_variable_emoji_key("freeze"), "effect_freeze")
        self.assertEqual(guide_variable_emoji_key("efpoison"), "effect_poison")
        references = {entry.key: entry for entry in reference_entries()}
        self.assertEqual(references["effect_taunt"].category, "effect")
        self.assertEqual(references["effect_taunt"].variable, "{EF_taunt}")


class GuideCategoryBrowserTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def draft(name: str, alias: str, categories: list[str]) -> GuideDraft:
        return GuideDraft(
            editor_id=1,
            name=name,
            aliases=[alias],
            categories=categories,
            authors="Guide Expert",
            description="A categorized guide.",
        )

    async def test_categories_are_normalized_counted_and_browsable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            first = store.save(self.draft("Solo Finisher", "solo", ["One Boss", "Finisher"]), 1)
            store.save(self.draft("Another Solo", "solo2", ["one   boss"]), 1)
            third = store.save(self.draft("Three Boss Opener", "opener", ["Three Bosses"]), 1)

            categories = store.list_categories()
            counts = {key: count for _label, key, count in categories}
            self.assertEqual(counts["one boss"], 2)
            self.assertEqual(counts["three bosses"], 1)
            self.assertEqual(store.get(first.guide_id).name, "Solo Finisher")
            self.assertEqual(
                {guide.guide_id for guide in store.list_guides_by_category("ONE BOSS")},
                {first.guide_id, first.guide_id + 1},
            )

            cog = TeamGuides.__new__(TeamGuides)
            cog.store = store
            cog.bot = SimpleNamespace(ui_emoji_manager=None)
            browser = await GuideBrowserView.create(cog)
            self.assertEqual(len(browser.children), 2)
            self.assertLessEqual(len(browser.children[0].options), 25)
            index = next(
                index
                for index, (_label, key, _count) in enumerate(browser.categories)
                if key == "three bosses"
            )
            await browser.select_category(index)
            self.assertEqual([guide.guide_id for guide in browser.guides], [third.guide_id])
            self.assertIn("Three Bosses", browser.build_embed().title)


if __name__ == "__main__":
    unittest.main()