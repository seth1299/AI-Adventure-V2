import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_adventure.ai import gemini_service as g
from ai_adventure.application.opening_request import OpeningRequest
from tests.AI_Tests import test_gemini_service as fixtures


class OpeningRequestTests(unittest.TestCase):
    def test_blank_opening_is_repaired_before_parsing_or_checkpoint_approval(self):
        packet = fixtures.GeminiServiceTests._completed_new_game_packet(start_location_mode="exact")
        transport = g.build_new_game_phase_schema(packet, "opening_prose")
        contract = g.build_new_game_phase_schema(packet, "opening_prose", for_api=False)
        opening = {"introductory_message": "Morning light reaches the forest.",
                   "suggested_actions": ["Look around.", "Check your gear.", "Walk ahead."],
                   "opening_cues": []}
        for blank in ("", " \n\t"):
            with self.subTest(blank=blank), self.assertLogs(g.LOGGER, level="WARNING"):
                request = Mock(side_effect=[SimpleNamespace(output_text=json.dumps({
                    **opening, "introductory_message": blank})),
                    SimpleNamespace(output_text=json.dumps(opening))])
                text = g._generate_new_game_response_with_quality_retry(
                    SimpleNamespace(interactions=SimpleNamespace(create=request)),
                    model=g.DEFAULT_GEMINI_MODEL, prompt="Saved opening prompt",
                    response_schema=transport, validation_schema=contract,
                    config={"response_format": [{"type": "text", "mime_type": "application/json", "schema": transport}]},
                )
                self.assertEqual(request.call_count, 2)
                self.assertIn("introductory_message", request.call_args.kwargs["input"])
                self.assertEqual(json.loads(text), opening)

    def test_retry_reuses_approved_phases_and_exact_saved_opening_prompt(self):
        packet = fixtures.GeminiServiceTests._completed_new_game_packet(start_location_mode="exact")
        full = fixtures.GeminiServiceTests._complete_new_game_response()
        full.update(miscellaneous=[], bestiary=[], opening_cues=[])

        def phase(name):
            keys = g.build_new_game_phase_schema(packet, name, for_api=False)["properties"]
            return json.dumps({key: value for key, value in full.items() if key in keys})

        service = g.GeminiNarrationService(g.GeminiSettings(api_key="never-save-this-key"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new_game_prompt.txt"
            checkpoint = OpeningRequest(path, packet)
            with patch.object(g, "create_gemini_client"), \
                    patch.object(g, "_generate_new_game_response_with_quality_retry", side_effect=[
                        phase("world_skeleton"), phase("entities_mechanics"), g.GeminiRequestError("Unavailable")]), \
                    patch("ai_adventure.ai.request_metrics._append_benchmark_record"):
                with self.assertRaises(g.GeminiRequestError):
                    service.generate_new_game_world_staged(packet, opening_request=checkpoint)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("never-save-this-key", path.read_text(encoding="utf-8"))
            self.assertIn("response", stored["phases"]["world_skeleton"])
            self.assertIn("response", stored["phases"]["entities_mechanics"])
            self.assertNotIn("response", stored["phases"]["opening_prose"])
            opening = stored["phases"]["opening_prose"]
            reloaded = OpeningRequest(path)
            with patch.object(g, "create_gemini_client"), \
                    patch.object(g, "build_gemini_new_game_phase_prompt", side_effect=AssertionError("Prompt rebuilt")), \
                    patch.object(g, "_generate_new_game_response_with_quality_retry",
                                 return_value=phase("opening_prose")) as request, \
                    patch("ai_adventure.ai.request_metrics._append_benchmark_record"):
                result = service.generate_new_game_world_staged(reloaded.setup_packet, opening_request=reloaded)
            request.assert_called_once()
            self.assertEqual(request.call_args.kwargs["prompt"], opening["prompt"])
            self.assertEqual(request.call_args.kwargs["config"], opening["config"])
            self.assertEqual(request.call_args.kwargs["request_label"], "new-game opening_prose")
            self.assertIn("Morning light", result.introductory_message)
            self.assertIn("response", OpeningRequest(path).data["phases"]["opening_prose"])
            # Delivery, rather than provider success alone, owns deletion.
            self.assertTrue(path.is_file())

    def test_corrupt_checkpoint_is_preserved_instead_of_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new_game_prompt.txt"
            path.write_text("unfinished request", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                OpeningRequest(path, {"setup": {}})
            self.assertEqual(path.read_text(encoding="utf-8"), "unfinished request")

    def test_inventory_review_precedes_opening_and_survives_cancel_and_retry(self):
        packet = fixtures.GeminiServiceTests._completed_new_game_packet(start_location_mode="exact")
        full = fixtures.GeminiServiceTests._complete_new_game_response()
        full.update(miscellaneous=[], bestiary=[], opening_cues=[])
        def phase(name):
            keys = g.build_new_game_phase_schema(packet, name, for_api=False)["properties"]
            return json.dumps({key: value for key, value in full.items() if key in keys})

        service = g.GeminiNarrationService(g.GeminiSettings(api_key="test-key"))
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = OpeningRequest(Path(directory) / "request.txt", packet)
            def cancel(state, setup, previous):
                self.assertIn("starting_items", state)
                self.assertNotIn("opening_prose", checkpoint.data["phases"])
                self.assertIsNone(previous)
                return None
            with patch.object(g, "create_gemini_client"), \
                    patch.object(g, "_generate_new_game_response_with_quality_retry", side_effect=[
                        phase("world_skeleton"), phase("entities_mechanics")]) as requests, \
                    patch("ai_adventure.ai.request_metrics._append_benchmark_record"):
                with self.assertRaisesRegex(g.GeminiRequestError, "review was cancelled"):
                    service.generate_new_game_world_staged(packet, opening_request=checkpoint,
                                                          inventory_review_callback=cancel)
                self.assertEqual(requests.call_count, 2)
            with patch.object(g, "create_gemini_client"), \
                    patch.object(g, "_generate_new_game_response_with_quality_retry",
                                 return_value=phase("opening_prose")) as requests, \
                    patch("ai_adventure.ai.request_metrics._append_benchmark_record"):
                result = service.generate_new_game_world_staged(packet, opening_request=checkpoint,
                    inventory_review_callback=lambda *_args: [])
                self.assertEqual(requests.call_count, 1)
                self.assertEqual(requests.call_args.kwargs["request_label"], "new-game opening_prose")
                self.assertIn('"starting_items":[]', requests.call_args.kwargs["prompt"])
            self.assertEqual(result.approved_starter_items, [])
            self.assertEqual(OpeningRequest(checkpoint.path).data["starting_inventory"], [])

    def test_player_inventory_edit_invalidates_only_prior_opening_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = OpeningRequest(Path(directory) / "request.txt", {"setup": {}})
            checkpoint.data["phases"] = {"world_skeleton": {"response": "world"},
                "entities_mechanics": {"response": "entities"}, "opening_prose": {"response": "old prose"}}
            checkpoint.approve_starting_inventory([])
            self.assertNotIn("opening_prose", checkpoint.data["phases"])
            self.assertEqual(checkpoint.data["phases"]["entities_mechanics"]["response"], "entities")
            checkpoint.data["phases"]["opening_prose"] = {"response": "new prose"}
            checkpoint.approve_starting_inventory([])
            self.assertIn("opening_prose", checkpoint.data["phases"])
