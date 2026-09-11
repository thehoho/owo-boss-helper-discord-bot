from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.emoji_assets import emoji_label
from cogs.game_catalog import EFFECTS, resolve_effect
from cogs.team_guides import (
    GuideBrowserView,
    GuideCategorySelect,
    GuideDraft,
    GuideEntrySelect,
    TeamGuideStore,
    TeamGuides,
    guide_variable_emoji_key,
)
from cogs.ui_emojis import emoji_asset_keys


class CompleteEffectCatalogTests(unittest.TestCase):
    def test_all_22_support_server_effects_are_registered_and_portable(self) -> None:
        expected = {
            "attack_up",
            "attack_up_plus",
            "attack_up_plus_plus",
            "celebration",
            "defense_up",
            "exposed",
            "flame",
            "freeze",
            "frostbite",
            "heavy_arrow",
            "leech",
            "mortality",
            "pest",
            "poison",
            "sacred_ward",
            "sin",
            "spellskin",
            "stinky",
            "stoneskin",
            "taunt",
            "tether",
            "virtue",
        }
        self.assertEqual({entry.key for entry in EFFECTS}, expected)
        self.assertTrue(
            {f"effect_{key}" for key in expected}.issubset(emoji_asset_keys())
        )
        self.assertEqual(emoji_label("effect_sacred_ward"), "EF_sacred_ward")
        aliases = {
            "heavyarrow": "effect_heavy_arrow",
            "sacredward": "effect_sacred_ward",
            "spell_skin": "effect_spellskin",
            "stone_skin": "effect_stoneskin",
            "trtether": "effect_tether",
            "vir": "effect_virtue",
        }
        for alias, key in aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(resolve_effect(alias).emoji_key, key)
                self.assertEqual(guide_variable_emoji_key(alias), key)


class GuideBrowserInteractionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def interaction() -> SimpleNamespace:
        return SimpleNamespace(
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
            ),
            edit_original_response=AsyncMock(),
        )

    async def test_category_rebuild_and_private_guide_open_are_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            guide = store.save(
                GuideDraft(
                    editor_id=1,
                    name="Solo Finisher",
                    aliases=["solo"],
                    categories=["One Boss"],
                    authors="Guide Expert",
                    description="A categorized guide.",
                ),
                1,
            )
            cog = TeamGuides.__new__(TeamGuides)
            cog.store = store
            cog.bot = SimpleNamespace(ui_emoji_manager=None)
            browser = await GuideBrowserView.create(cog)

            category = next(
                item for item in browser.children if isinstance(item, GuideCategorySelect)
            )
            category_index = next(
                index
                for index, (_label, key, _count) in enumerate(browser.categories)
                if key == "one boss"
            )
            category._values = [f"category:{category_index}"]
            category_interaction = self.interaction()
            await category.callback(category_interaction)

            category_interaction.response.defer.assert_awaited_once_with()
            category_interaction.edit_original_response.assert_awaited_once()
            self.assertIsNone(category.view)
            self.assertEqual([item.guide_id for item in browser.guides], [guide.guide_id])

            entry = next(
                item for item in browser.children if isinstance(item, GuideEntrySelect)
            )
            entry._values = [str(guide.guide_id)]
            guide_interaction = self.interaction()
            await entry.callback(guide_interaction)

            guide_interaction.response.defer.assert_awaited_once_with(
                ephemeral=True,
                thinking=True,
            )
            guide_interaction.edit_original_response.assert_awaited_once()
            kwargs = guide_interaction.edit_original_response.await_args.kwargs
            self.assertTrue(kwargs["embed"].title.startswith("Solo Finisher"))
            self.assertIsNotNone(kwargs["view"])


if __name__ == "__main__":
    unittest.main()
