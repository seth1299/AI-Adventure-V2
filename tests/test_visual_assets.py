from __future__ import annotations

from io import BytesIO
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from PIL import Image

from ai_adventure.ai.gemini_service import (
    NEW_GAME_RESPONSE_JSON_SCHEMA,
    build_gemini_new_game_prompt,
)
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.visual_assets import (
    DISPLAY_IMAGE_MAX_PIXELS,
    GeminiVisualAssetService,
    VisualAssetRequest,
    build_visual_asset_requests,
    find_reusable_inventory_asset,
    save_relative_image_filename,
    descriptive_image_stem,
    save_scaled_jpeg,
)


class _VisualRepository:
    def get_setting(self, key: str, default=None):
        return {
            "player_name": "Kit Vale",
            "player.appearance": "A short scout in pale armor with a long rifle.",
        }.get(key, default)

    def list_history(self):
        return [{"kind": "story", "message_id": "opening", "content": "Begin."}]

    def list_mechanical_events(self):
        return [
            {
                "event_type": "LocationUpsertedEvent",
                "payload": {"name": "Glass Market"},
                "status": "applied",
                "message_id": "turn-1",
            },
            {
                "event_type": "InventoryItemAddedEvent",
                "payload": {"item_name": "Ripe Banana"},
                "status": "applied",
                "message_id": "turn-1",
            },
            {
                "event_type": "NpcUpsertedEvent",
                "payload": {"npc_id": "dock_warden"},
                "status": "applied",
                "message_id": "turn-2",
            },
        ]

    def ensure_travel_locations(self):
        return [{"name": "Glass Market", "description": "Blue awnings over wet stone."}]

    def list_inventory_items(self):
        return [
            {
                "name": "Ripe Banana",
                "category": "Food",
                "description": "A curved yellow fruit with a few brown freckles.",
            }
        ]

    def list_player_visible_npcs(self, limit=50):
        return [
            {
                "npc_id": "dock_warden",
                "display_name": "Dock Warden",
                "description": "A broad woman in an orange rain cape.",
                "notes": "Keeps order at the piers.",
            }
        ][:limit]

    def list_bestiary_entries(self):
        return []


class VisualAssetTests(unittest.TestCase):
    def test_image_service_uses_image_only_model_request(self) -> None:
        captured: dict[str, object] = {}

        class _ImageConfig:
            def __init__(self, **kwargs):
                self.values = kwargs

        class _GenerateContentConfig:
            def __init__(self, **kwargs):
                self.values = kwargs

        class _Models:
            def generate_content(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    parts=[
                        types.SimpleNamespace(
                            inline_data=types.SimpleNamespace(
                                data=b"image-bytes",
                                mime_type="image/png",
                            )
                        )
                    ]
                )

        fake_genai = types.ModuleType("google.genai")
        setattr(
            fake_genai,
            "Client",
            lambda **_kwargs: types.SimpleNamespace(models=_Models()),
        )
        fake_types = types.ModuleType("google.genai.types")
        setattr(fake_types, "ImageConfig", _ImageConfig)
        setattr(fake_types, "GenerateContentConfig", _GenerateContentConfig)
        setattr(fake_genai, "types", fake_types)
        fake_google = types.ModuleType("google")
        setattr(fake_google, "genai", fake_genai)
        request = VisualAssetRequest(
            subject_type="location",
            subject_key="glass market",
            display_name="Glass Market",
            description="Blue awnings over wet stone.",
        )

        with (
            patch.dict(
                "sys.modules",
                {
                    "google": fake_google,
                    "google.genai": fake_genai,
                    "google.genai.types": fake_types,
                },
            ),
            patch("ai_adventure.visual_assets.read_api_key", return_value="key"),
        ):
            data, mime_type = GeminiVisualAssetService(
                api_key_path=Path("api-key.txt"),
            ).generate(request)

        self.assertEqual(data, b"image-bytes")
        self.assertEqual(mime_type, "image/png")
        self.assertEqual(captured["model"], "gemini-3.1-flash-lite-image")
        config = cast(Any, captured["config"])
        self.assertEqual(config.values["response_modalities"], ["IMAGE"])
        self.assertEqual(config.values["image_config"].values["aspect_ratio"], "16:9")

    def test_new_game_contract_mentions_visual_quality_without_image_schema_fields(self) -> None:
        prompt = build_gemini_new_game_prompt(
            {"packet_type": "new_game_setup", "requirements": {}}
        )
        schema_text = str(NEW_GAME_RESPONSE_JSON_SCHEMA).casefold()

        self.assertIn("do not add image fields", prompt.casefold())
        self.assertNotIn("image_prompt", schema_text)
        self.assertNotIn("image_url", schema_text)
        self.assertNotIn("image_base64", schema_text)

    def test_requests_cover_all_surfaces_and_link_introducing_messages(self) -> None:
        requests = build_visual_asset_requests(_VisualRepository())
        by_subject = {(request.subject_type, request.subject_key): request for request in requests}

        self.assertEqual(
            {subject_type for subject_type, _key in by_subject},
            {"player", "location", "inventory", "npc"},
        )
        self.assertEqual(by_subject[("player", "player_character")].message_ids, ("opening",))
        self.assertEqual(by_subject[("location", "glass market")].message_ids, ("turn-1",))
        self.assertEqual(by_subject[("inventory", "ripe banana")].message_ids, ("turn-1",))
        self.assertEqual(by_subject[("npc", "dock_warden")].message_ids, ("turn-2",))

    def test_requests_include_player_known_bestiary_creatures(self) -> None:
        class _BestiaryRepository(_VisualRepository):
            def list_bestiary_entries(self):
                return [
                    {
                        "creature_id": "mist_strider",
                        "name": "Mist-Strider",
                        "details": "A tall six-legged creature with translucent fur.",
                    }
                ]

            def list_mechanical_events(self):
                return [
                    *super().list_mechanical_events(),
                    {
                        "event_type": "BestiaryEntryUpsertedEvent",
                        "payload": {
                            "creature_id": "mist_strider",
                            "name": "Mist-Strider",
                        },
                        "status": "applied",
                        "message_id": "turn-3",
                    },
                ]

        requests = build_visual_asset_requests(_BestiaryRepository())
        creature = next(request for request in requests if request.subject_type == "bestiary")

        self.assertEqual(creature.subject_key, "mist_strider")
        self.assertEqual(creature.message_ids, ("turn-3",))
        self.assertEqual(creature.aspect_ratio, "1:1")
        self.assertIn("single non-human creature illustration", creature.prompt)

    def test_image_prompt_passes_banned_terms_and_exact_map_labels(self) -> None:
        class _MapRepository(_VisualRepository):
            def ensure_travel_locations(self):
                return [
                    {
                        "name": "Riverbend City",
                        "description": "A river city.",
                        "x_miles": 0,
                        "y_miles": 0,
                    },
                    {
                        "name": "Dark Forest",
                        "description": "A forest east of the city.",
                        "x_miles": 8,
                        "y_miles": 0,
                    },
                    {"name": "Oakhaven", "description": "A forbidden example."},
                ]

            def list_inventory_items(self):
                return [
                    {
                        "name": "Basic Regional Map",
                        "category": "Document",
                        "description": (
                            "A hand-inked parchment map showing Riverbend City and "
                            "surrounding roads and forests."
                        ),
                    }
                ]

        request = next(
            request
            for request in build_visual_asset_requests(_MapRepository())
            if request.subject_type == "inventory"
        )

        self.assertIn("Alden", request.prompt)
        self.assertIn('"Riverbend City"', request.prompt)
        self.assertNotIn('"Oakhaven"', request.prompt)
        self.assertIn("do not add, rename, or imply any other place", request.prompt)
        self.assertIn("x_miles increases eastward (right)", request.prompt)
        self.assertIn('"Dark Forest" is east of "Riverbend City".', request.prompt)

    def test_filename_is_descriptive_bounded_and_versioned(self) -> None:
        request = VisualAssetRequest(
            subject_type="inventory",
            subject_key="ripe banana",
            display_name="Ripe Banana / Market Special!",
            description="A yellow banana.",
        )

        self.assertRegex(request.filename, r"^inventory_ripe_banana_market_special_[0-9a-f]{8}\.jpg$")
        self.assertLessEqual(len(request.filename), 77)
        self.assertEqual(descriptive_image_stem("***"), "generated_image")

    def test_image_prompt_carries_era_and_world_constraints(self) -> None:
        request = VisualAssetRequest(
            subject_type="inventory",
            subject_key="black sedan",
            display_name="Black Sedan",
            description="A plain black four-door car.",
            world_context=(
                "Genre: Detective mystery\n"
                "World context: Set in 1940; period-authentic vehicles only."
            ),
        )

        self.assertIn("1940", request.prompt)
        self.assertIn("period-authentic vehicles", request.prompt)
        self.assertIn("do not default to modern designs", request.prompt)

    def test_selected_style_is_in_every_prompt_and_asset_identity(self) -> None:
        digital = VisualAssetRequest(
            subject_type="location",
            subject_key="old_station",
            display_name="Old Station",
            description="A weathered rural station with faded paint.",
        )
        noir = VisualAssetRequest(
            subject_type="location",
            subject_key="old_station",
            display_name="Old Station",
            description="A weathered rural station with faded paint.",
            image_style="film_noir",
        )

        self.assertIn(
            "Selected visual style: film_noir (Film Noir)",
            noir.prompt,
        )
        self.assertIn("black-and-white film noir", noir.prompt)
        self.assertNotEqual(digital.asset_id, noir.asset_id)

    def test_identity_hash_ignores_volatile_world_summary(self) -> None:
        base = VisualAssetRequest(
            subject_type="player",
            subject_key="player_123",
            display_name="Avery Stone",
            description="A weathered traveler in a blue coat.",
            world_context="Genre: Mystery",
        )
        with_summary = VisualAssetRequest(
            subject_type="player",
            subject_key="player_123",
            display_name="Avery Stone",
            description="A weathered traveler in a blue coat.",
            world_context="Genre: Mystery\nPlayer-known world summary: A large city.",
        )

        self.assertEqual(base.asset_id, with_summary.asset_id)
        self.assertEqual(base.descriptor_hash, with_summary.descriptor_hash)

    def test_requests_apply_the_saved_style_to_every_subject(self) -> None:
        class _StyledRepository(_VisualRepository):
            def get_setting(self, key: str, default=None):
                if key == "images.style":
                    return "watercolor"
                return super().get_setting(key, default)

        requests = build_visual_asset_requests(_StyledRepository())

        self.assertTrue(requests)
        self.assertEqual({request.image_style for request in requests}, {"watercolor"})
        self.assertTrue(
            all(
                "Selected visual style: watercolor" in request.prompt
                for request in requests
            )
        )

    def test_requests_use_stable_entity_ids_and_save_grouped_filenames(self) -> None:
        class _IdentifiedRepository(_VisualRepository):
            db_path = Path("C:/saves/Example_Save/adventure.db")

            def get_setting(self, key: str, default=None):
                values = {
                    "player_name": "Kit Vale",
                    "player.appearance": "A short scout in pale armor.",
                    "player.id": "player_abc123",
                }
                return values.get(key, default)

            def ensure_travel_locations(self):
                return [
                    {
                        "name": "Glass Market",
                        "location_id": "loc_abc123",
                        "description": "Blue awnings over wet stone.",
                    }
                ]

            def list_inventory_items(self):
                return [
                    {
                        "name": "Canvas Backpack",
                        "category": "Container",
                        "description": "A worn canvas backpack.",
                        "metadata": {"item_uuid": "item_abc123"},
                    }
                ]

        requests = build_visual_asset_requests(_IdentifiedRepository())
        by_type = {request.subject_type: request for request in requests}
        self.assertEqual(by_type["player"].subject_key, "player_abc123")
        self.assertEqual(by_type["location"].subject_key, "loc_abc123")
        self.assertEqual(by_type["inventory"].subject_key, "item_abc123")
        self.assertEqual(
            save_relative_image_filename(_IdentifiedRepository(), by_type["inventory"]),
            "example_save/inventory_canvas_backpack_"
            + by_type["inventory"].descriptor_hash[:8]
            + ".jpg",
        )

    def test_fuzzy_reuse_finds_a_compatible_inventory_image_in_another_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            saves_dir = root / "saves"
            source_repository = SaveRepository.create_new_save(saves_dir, "Source")
            target_repository = SaveRepository.create_new_save(saves_dir, "Target")
            source_request = VisualAssetRequest(
                subject_type="inventory",
                subject_key="source_item",
                display_name="Canvas Backpack",
                description="Container. A worn canvas backpack with leather straps.",
            )
            source_filename = save_relative_image_filename(source_repository, source_request)
            source_path = root / "images" / source_filename
            source_path.parent.mkdir(parents=True)
            Image.new("RGB", (32, 32), (10, 20, 30)).save(source_path, format="JPEG")
            source_repository.ensure_visual_asset(
                asset_id=source_request.asset_id,
                subject_type=source_request.subject_type,
                subject_key=source_request.subject_key,
                display_name=source_request.display_name,
                descriptor_hash=source_request.descriptor_hash,
                filename=source_filename,
                prompt=source_request.prompt,
                model="test",
                ready=True,
            )

            target_request = VisualAssetRequest(
                subject_type="inventory",
                subject_key="target_item",
                display_name="Leather Canvas Backpack",
                description="Container. A worn canvas backpack with leather straps and a brass buckle.",
            )
            reusable = find_reusable_inventory_asset(
                images_dir=root / "images",
                saves_dir=saves_dir,
                repository=target_repository,
                request=target_request,
            )

            self.assertIsNotNone(reusable)
            assert reusable is not None
            self.assertEqual(reusable["source_path"], source_path)

            mismatched_style = VisualAssetRequest(
                subject_type="inventory",
                subject_key="target_item_oil",
                display_name="Leather Canvas Backpack",
                description=(
                    "Container. A worn canvas backpack with leather straps and a brass buckle."
                ),
                image_style="oil_painting",
            )
            self.assertIsNone(
                find_reusable_inventory_asset(
                    images_dir=root / "images",
                    saves_dir=saves_dir,
                    repository=target_repository,
                    request=mismatched_style,
                )
            )

    def test_save_assigns_player_and_location_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Identity Test")
            repository.set_travel_locations(
                [{"name": "Old Station", "description": "A weathered station."}]
            )

            player_id = repository.get_player_id()
            locations = repository.get_travel_locations()

            self.assertRegex(player_id, r"^player_[0-9a-f]{32}$")
            self.assertRegex(str(locations[0]["location_id"]), r"^loc_[0-9a-f]{32}$")
            self.assertEqual(locations[0]["name"], "Old Station")

    def test_location_and_item_prompts_keep_the_subject_free_of_people(self) -> None:
        location_prompt = VisualAssetRequest(
            subject_type="location",
            subject_key="homely town",
            display_name="Homely Town",
            description="A welcoming town with warm lights and narrow streets.",
        ).prompt.casefold()
        item_prompt = VisualAssetRequest(
            subject_type="inventory",
            subject_key="silver pen",
            display_name="Silver Pen",
            description="A slim silver pen with a blue enamel cap.",
        ).prompt.casefold()

        self.assertIn("do not include any people", location_prompt)
        self.assertIn("never as a request to depict a human", location_prompt)
        self.assertIn("by itself", item_prompt)
        self.assertIn("do not show a person, face, body, hand, arm", item_prompt)

    def test_image_prompts_avoid_synthetic_visual_tells(self) -> None:
        prompt = VisualAssetRequest(
            subject_type="location",
            subject_key="old_station",
            display_name="Old Station",
            description="A weathered rural station with faded paint.",
        ).prompt.casefold()

        for phrase in (
            "avoid excessive drop shadows",
            "perfect symmetry",
            "dramatic cinematic color grading",
            "overly saturated colors",
            "unnaturally clean or flawless surfaces",
            "small imperfections",
        ):
            self.assertIn(phrase, prompt)

    def test_scaled_cache_is_a_small_rgb_jpeg(self) -> None:
        source = Image.new("RGBA", (1200, 800), (240, 220, 30, 255))
        image_bytes = BytesIO()
        source.save(image_bytes, format="PNG")

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ripe_banana.jpg"
            width, height = save_scaled_jpeg(image_bytes.getvalue(), target)

            self.assertLessEqual(max(width, height), DISPLAY_IMAGE_MAX_PIXELS)
            with Image.open(target) as saved:
                self.assertEqual(saved.format, "JPEG")
                self.assertEqual(saved.mode, "RGB")
                self.assertEqual(saved.size, (width, height))

    def test_repository_tracks_reuse_messages_failures_and_paid_attempts(self) -> None:
        request = VisualAssetRequest(
            subject_type="inventory",
            subject_key="ripe banana",
            display_name="Ripe Banana",
            description="A curved yellow fruit.",
            message_ids=("turn-1",),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Images")
            record = repository.ensure_visual_asset(
                asset_id=request.asset_id,
                subject_type=request.subject_type,
                subject_key=request.subject_key,
                display_name=request.display_name,
                descriptor_hash=request.descriptor_hash,
                filename=request.filename,
                prompt=request.prompt,
                model="gemini-3.1-flash-lite-image",
                message_ids=request.message_ids,
            )
            self.assertEqual(record["status"], "queued")

            repository.set_visual_asset_status(request.asset_id, "generating")
            repository.set_visual_asset_status(request.asset_id, "failed", error_message="quota")
            self.assertEqual(repository.visual_asset_generation_count(), 1)
            self.assertEqual(repository.reset_failed_visual_assets(), 1)

            repository.set_visual_asset_status(request.asset_id, "generating")
            self.assertEqual(repository.visual_asset_generation_count(), 2)

            repository.set_visual_asset_status(
                request.asset_id,
                "ready",
                width=384,
                height=384,
            )
            ready = repository.get_visual_asset("inventory", "RIPE BANANA")
            self.assertIsNotNone(ready)
            assert ready is not None
            self.assertEqual(ready["filename"], request.filename)
            linked = repository.list_visual_assets_for_message("turn-1")
            self.assertEqual([asset["asset_id"] for asset in linked], [request.asset_id])


if __name__ == "__main__":
    unittest.main()
