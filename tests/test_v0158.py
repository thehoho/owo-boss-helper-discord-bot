from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.bot_info import BOT_VERSION
from cogs.helper_prefix import parse_helper_command_argument
from cogs.team_guides import (
    GUIDE_APPLICATION_EMOJI_RESERVE,
    GUIDE_SPECIAL_EMOJI_LIMIT,
    GuideAliasConflict,
    GuideDraft,
    GuideEditorView,
    GuideSlot,
    TeamGuideStore,
    TeamGuides,
    animal_emoji_key,
    build_guide_system_help_embed,
    guide_variable_emoji_key,
)
from cogs.ui_emojis import DEX_ARTWORK, clear_emoji_catalog_cache


def complete_draft(editor_id: int, name: str, alias: str) -> GuideDraft:
    return GuideDraft(
        editor_id=editor_id,
        name=name,
        aliases=[alias],
        categories=["boss"],
        authors="Expert",
        description="A durable guide with {sword}.",
        full_guide="**Detailed** advice.",
        viability=5,
        ease=2,
        slots={
            1: GuideSlot(1, "special buddy", 50, "special", "sword + crit"),
            2: GuideSlot(2, "fish", None, "common", "bow + hp", "Second note"),
            3: GuideSlot(3, "wolf", 35, "rare", "shield + mr"),
        },
    )


class GuideDraftPersistenceTests(unittest.TestCase):
    def test_draft_round_trip_survives_store_reopen_and_deletes_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "team_guides.db"
            store = TeamGuideStore(path)
            store.initialize()
            original = complete_draft(42, "Saved Guide", "saved")
            updated_at = store.save_draft(original)

            reopened = TeamGuideStore(path)
            reopened.initialize()
            loaded = reopened.load_draft(42)

            self.assertIsNotNone(loaded)
            restored, restored_at = loaded
            self.assertEqual(restored_at, updated_at)
            self.assertEqual(restored.name, original.name)
            self.assertEqual(restored.aliases, original.aliases)
            self.assertEqual(restored.full_guide, original.full_guide)
            self.assertEqual(restored.slots, original.slots)
            self.assertTrue(reopened.delete_draft(42))
            self.assertIsNone(reopened.load_draft(42))

    def test_alias_conflict_identifies_existing_guide_and_keeps_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            existing = store.save(complete_draft(1, "Existing Team", "shared"), 1)
            candidate = complete_draft(2, "New Team", "shared")
            store.save_draft(candidate)

            with self.assertRaises(GuideAliasConflict) as caught:
                store.save(candidate, 2)

            self.assertEqual(caught.exception.alias, "shared")
            self.assertEqual(caught.exception.guide_id, existing.guide_id)
            self.assertEqual(caught.exception.guide_name, "Existing Team")
            self.assertIsNotNone(store.load_draft(2))


class GuideSpecialAnimalTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        DEX_ARTWORK.pop("pet_special_buddy", None)
        clear_emoji_catalog_cache()

    async def test_persisted_dex_alias_resolves_in_slots_and_variables(self) -> None:
        DEX_ARTWORK["pet_special_buddy"] = (
            b"image",
            "Special Buddy",
            '["stest", "buddy alias"]',
            "https://cdn.discordapp.com/attachments/example.png",
        )
        clear_emoji_catalog_cache()

        self.assertEqual(animal_emoji_key("stest"), "pet_special_buddy")
        self.assertEqual(guide_variable_emoji_key("buddy alias"), "pet_special_buddy")

    async def test_only_referenced_exact_special_alias_is_imported_once(self) -> None:
        record = SimpleNamespace(
            animal_key="special_buddy",
            display_name="Special Buddy",
            aliases=("special buddy", "buddy alias"),
            rank="special",
        )
        manager = SimpleNamespace(emojis={}, ensure_synced=AsyncMock())
        bot = SimpleNamespace(
            animal_dex_store=SimpleNamespace(find=lambda query: record),
            ui_emoji_manager=manager,
            fetch_application_emojis=AsyncMock(return_value=[]),
        )
        cog = TeamGuides.__new__(TeamGuides)
        cog.bot = bot
        draft = complete_draft(1, "Special Guide", "special")
        draft.description = "Use {buddy alias}."

        with patch("cogs.team_guides.import_dex_record", new=AsyncMock(return_value="pet_special_buddy")) as importer:
            prepared, failures = await cog.ensure_draft_special_animal_emojis(draft)

        self.assertEqual(prepared, ("Special Buddy",))
        self.assertEqual(failures, ())
        importer.assert_awaited_once()

    async def test_capacity_reserve_blocks_upload_without_losing_draft(self) -> None:
        record = SimpleNamespace(
            animal_key="special_buddy",
            display_name="Special Buddy",
            aliases=("special buddy",),
            rank="special",
        )
        manager = SimpleNamespace(emojis={}, ensure_synced=AsyncMock())
        inventory = [SimpleNamespace(id=index) for index in range(2000 - GUIDE_APPLICATION_EMOJI_RESERVE)]
        bot = SimpleNamespace(
            animal_dex_store=SimpleNamespace(find=lambda query: record),
            ui_emoji_manager=manager,
            fetch_application_emojis=AsyncMock(return_value=inventory),
        )
        cog = TeamGuides.__new__(TeamGuides)
        cog.bot = bot

        with patch("cogs.team_guides.import_dex_record", new=AsyncMock()) as importer:
            prepared, failures = await cog.ensure_draft_special_animal_emojis(
                complete_draft(1, "Capacity Guide", "capacity")
            )

        self.assertEqual(prepared, ())
        self.assertTrue(any(str(GUIDE_APPLICATION_EMOJI_RESERVE) in item for item in failures))
        importer.assert_not_awaited()


class GuideHelpSurfaceTests(unittest.TestCase):
    def test_help_embed_documents_complete_workflow_within_discord_limits(self) -> None:
        embed = build_guide_system_help_embed(SimpleNamespace(), "!")
        combined = " ".join(
            [embed.title or "", embed.description or ""]
            + [f"{field.name} {field.value}" for field in embed.fields]
        )
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        for expected in (
            "! guide help",
            "/team-guide-create",
            "/animal-dex",
            "/guide-emojis",
            "Markdown",
            "auto-saved",
            "weapon + passive @ rank",
            "Review and publish",
        ):
            self.assertIn(expected, combined)
        self.assertNotIn("application-emoji slots", combined)
        self.assertNotIn("guide-only imports are capped", combined)
        self.assertEqual(GUIDE_SPECIAL_EMOJI_LIMIT, 300)

    def test_both_text_help_forms_and_custom_prefix_are_accepted(self) -> None:
        aliases = {"h guide help", "hguide help", "h help guide", "hhelp guide"}
        self.assertEqual(parse_helper_command_argument("h guide help", "h", aliases), "")
        self.assertEqual(parse_helper_command_argument("h help guide", "h", aliases), "")
        self.assertEqual(parse_helper_command_argument("!guide help", "!", aliases), "")
        self.assertEqual(parse_helper_command_argument("! help guide", "!", aliases), "")

    def test_editor_exposes_durable_draft_controls(self) -> None:
        editor = GuideEditorView(
            SimpleNamespace(bot=SimpleNamespace(ui_emoji_manager=None)),
            GuideDraft(editor_id=1),
        )
        labels = {str(item.label) for item in editor.children if getattr(item, "label", None)}
        self.assertEqual(len(editor.children), 12)
        self.assertTrue({"Save draft", "Close", "Discard draft"}.issubset(labels))
        self.assertEqual(TeamGuides.team_guide_help.name, "team-guide-help")

    def test_public_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.9-beta")


if __name__ == "__main__":
    unittest.main()