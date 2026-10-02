from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_adventure.ai.gemini_service import (
    AiNarrationResult, GeminiNarrationService, GeminiSettings, GeminiRequestError,
    build_gemini_story_prompt,
)
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.items import normalize_item_metadata
from ai_adventure.container_flow import ContainerFlowError, container_event_issues
from ai_adventure.events.event_applier import EventApplier, AppliedEventResult
from ai_adventure.persistence.save_repository import SaveRepository


def content_item(name, category="Item"):
    return {"name": name, "category": category, "quantity": 1,
            "description": f"A small {name.lower()}.", "value_base_units": 5}


def corrected_response():
    return {
        "response": "You open the satchel and find five silver coins, a glass vial, and a folded map. "
                    "You take the coins and vial, leaving the map inside.",
        "suggested_actions": [], "out_of_game": False,
        "events": [
            {"type": "ContainerOpenedEvent", "payload": {"container_name": "Silver-Trimmed Satchel",
                "contents": {"currency_base_units": 50, "items": [content_item("Glass Vial", "Container"), content_item("City Map")]}}},
            {"type": "ContainerContentsTakenEvent", "payload": {"container_name": "Silver-Trimmed Satchel",
                "item_names": ["Glass Vial"], "take_currency": True}},
            {"type": "StatusUpdatedEvent", "payload": {"location": "AUTO", "minutes_passed": 5, "weather": "AUTO"}},
        ],
    }


def broken_response():
    response = corrected_response()
    response["events"][0]["payload"].pop("contents")
    response["events"][1]["payload"] = {"container_name": "Silver-Trimmed Satchel"}
    response["events"].insert(2, {"type": "InventoryItemAddedEvent", "payload": {
        "item_type": "Container", "item_name": "Glass Vial", "description": "A small glass vial.",
        "amount": 1, "value_base_units": 5,
    }})
    return response


class ContainerConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.directory.name), "Container regression")
        self.repository.set_state_value("currency.balance", "20")
        self.repository.add_inventory_item("Silver-Trimmed Satchel", "Container", 1, "A stolen satchel.", 50)

    def packet(self):
        return StoryTurnService.build_context_packet(self.repository, "Open the satchel and take the coins and vial, leaving the map.")

    def generate(self, responses, *, packet=None):
        import google.genai
        client = Mock()
        client.models.generate_content.side_effect = [SimpleNamespace(text=json.dumps(response)) for response in responses]
        with patch.object(google.genai, "Client", return_value=client):
            result = GeminiNarrationService(GeminiSettings(api_key="test-key", model="gemini-2.5-flash")).generate_story_response(packet or self.packet())
        return result, client

    def test_unknown_is_distinct_from_explicit_empty(self):
        unknown = normalize_item_metadata({"item_type": "Container"})["container"]
        empty = normalize_item_metadata({"item_type": "Container", "container": {
            "contents": {"currency_base_units": 0, "items": []}}})["container"]
        self.assertFalse(unknown["contents_initialized"])
        self.assertTrue(empty["contents_initialized"])

    def test_original_failure_is_repaired_before_any_commit(self):
        result, client = self.generate([broken_response(), corrected_response()])
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertEqual(self.repository.get_state_value("currency.balance"), "20")
        StoryTurnService.commit_response(self.repository, result, message_id="repaired-turn")
        self.assertEqual(self.repository.get_state_value("currency.balance"), "70")
        inventory = {item["name"]: item for item in self.repository.list_inventory_items()}
        self.assertEqual(inventory["Glass Vial"]["quantity"], 1)
        self.assertEqual(inventory["City Map"]["storage_location"], "Silver-Trimmed Satchel")
        container = inventory["Silver-Trimmed Satchel"]["metadata"]["container"]
        self.assertTrue(container["contents_initialized"])
        self.assertFalse(container["contents_taken"])
        self.assertEqual(container["contents"]["currency_base_units"], 0)
        self.assertEqual([next(row["name"] for row in self.repository.list_item_catalog() if row["id"] == item_id) for item_id in container["contents"]["items"]], ["City Map"])
        events = self.repository.list_mechanical_events()
        transferred = next(event for event in events if event["event_type"] == "ContainerContentsTakenEvent")
        self.assertEqual(transferred["payload"]["currency_base_units"], 50)
        self.assertEqual([item["name"] for item in transferred["payload"]["items"]], ["Glass Vial"])

    def test_valid_single_response_needs_no_extra_request(self):
        result, client = self.generate([corrected_response()])
        self.assertEqual(client.models.generate_content.call_count, 1)
        self.assertIn("take the coins and vial", result.narrative_text)

    def test_repair_metrics_record_additional_request(self):
        with self.assertLogs("ai_adventure.ai.request_metrics", level="INFO") as logs:
            self.generate([broken_response(), corrected_response()])
        metrics = json.loads(logs.output[-1].split("Gemini operation metrics: ")[1])
        self.assertEqual(metrics["request_count"], 2)
        self.assertEqual(metrics["repair_count"], 1)

    def test_known_empty_container_cannot_promise_or_transfer_coins(self):
        response = corrected_response()
        response["events"][0]["payload"]["contents"] = {"currency_base_units": 0, "items": []}
        response["events"][1]["payload"] = {"container_name": "Silver-Trimmed Satchel"}
        self.assertTrue(container_event_issues(response["events"], self.packet(), narrative_text=response["response"]))
        self.assertFalse(container_event_issues(response["events"], self.packet(), narrative_text="The satchel is empty. There are no coins inside."))

    def test_omitting_all_container_events_does_not_bypass_repair(self):
        response = corrected_response()
        response["events"] = response["events"][2:]
        self.assertTrue(container_event_issues(response["events"], self.packet(), narrative_text=response["response"]))
        result, client = self.generate([response, corrected_response()])
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertTrue(any(event["type"] == "ContainerOpenedEvent" for event in result.suggested_events))

    def test_nested_empty_vial_remains_known_empty_after_transfer(self):
        response = corrected_response()
        response["events"][0]["payload"]["contents"]["items"][0]["contents_initialized"] = True
        result, _ = self.generate([response])
        StoryTurnService.commit_response(self.repository, result, message_id="known-empty-vial")
        vial = next(item for item in self.repository.list_inventory_items() if item["name"] == "Glass Vial")
        self.assertTrue(vial["metadata"]["container"]["contents_initialized"])
        self.assertEqual(vial["metadata"]["container"]["contents"], {"currency_base_units": 0, "items": []})

    def test_failed_application_rolls_back_already_written_narration_and_events(self):
        before = self.repository.list_inventory_items()
        history_before = self.repository.count_history()
        result, _ = self.generate([corrected_response()])
        original = EventApplier.apply_event
        def skip_open(applier, event, **kwargs):
            if event["type"] == "ContainerOpenedEvent":
                return AppliedEventResult(event["type"], "skipped", "Injected container access failure.", event["payload"])
            return original(applier, event, **kwargs)
        with patch.object(EventApplier, "apply_event", skip_open), self.assertRaises(ContainerFlowError):
            StoryTurnService.commit_response(self.repository, result, message_id="rolled-back-container")
        self.assertEqual(self.repository.count_history(), history_before)
        self.assertEqual(self.repository.list_inventory_items(), before)
        self.assertEqual(self.repository.get_state_value("currency.balance"), "20")
        self.assertFalse(self.repository.list_mechanical_events())

    def test_existing_container_cannot_be_reset_via_inventory_addition(self):
        events = corrected_response()["events"]
        events.insert(0, {"type": "InventoryItemAddedEvent", "payload": {
            "item_name": "Silver-Trimmed Satchel", "item_type": "Container", "amount": 1,
            "description": "A reset satchel.", "value_base_units": 50,
        }})
        self.assertTrue(container_event_issues(events, self.packet()))
        applied = EventApplier(self.repository).apply_events(events)
        self.assertEqual(applied[0].status, "skipped")
        satchel = next(item for item in self.repository.list_inventory_items() if item["name"] == "Silver-Trimmed Satchel")
        self.assertEqual(satchel["quantity"], 1)

    def test_new_container_can_be_acquired_opened_and_selectively_taken_in_one_turn(self):
        response = corrected_response()
        for event in response["events"][:2]:
            event["payload"]["container_name"] = "New Satchel"
        response["events"].insert(0, {"type": "InventoryItemAddedEvent", "payload": {
            "item_name": "New Satchel", "item_type": "Container", "amount": 1,
            "description": "A newly acquired satchel.", "value_base_units": 50,
        }})
        result, client = self.generate([response])
        self.assertEqual(client.models.generate_content.call_count, 1)
        StoryTurnService.commit_response(self.repository, result, message_id="new-container")
        self.assertEqual(self.repository.get_state_value("currency.balance"), "70")
        satchel = next(item for item in self.repository.list_inventory_items() if item["name"] == "New Satchel")
        self.assertEqual([next(row["name"] for row in self.repository.list_item_catalog() if row["id"] == item_id) for item_id in satchel["metadata"]["container"]["contents"]["items"]], ["City Map"])

    def test_known_manifest_is_used_during_duplicate_reward_repair(self):
        EventApplier(self.repository).apply_event(corrected_response()["events"][0])
        response = corrected_response()
        response["events"][0]["payload"].pop("contents")
        invalid = json.loads(json.dumps(response))
        invalid["events"].insert(2, {"type": "CurrencyChangedEvent", "payload": {"base_unit_amount": 50}})
        packet = self.packet()
        packet["selection"]["tags"].append("currency")
        result, client = self.generate([invalid, response], packet=packet)
        self.assertEqual(client.models.generate_content.call_count, 2)
        StoryTurnService.commit_response(self.repository, result, message_id="known-container-repair")
        self.assertEqual(self.repository.get_state_value("currency.balance"), "70")

    def test_inspection_supplies_authority_even_without_inventory_planner_tag(self):
        EventApplier(self.repository).apply_event(corrected_response()["events"][0])
        packet = StoryTurnService.build_context_packet(self.repository, "Inspect the Silver-Trimmed Satchel.", planner_context_tags=["character"])
        self.assertTrue(packet["state"]["inventory"]["container_authority"])
        self.assertIn("City Map", build_gemini_story_prompt(packet))

    def test_invalid_repair_is_bounded_and_does_not_change_the_save(self):
        import google.genai
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(broken_response()))
        with patch.object(google.genai, "Client", return_value=client):
            with self.assertRaisesRegex(GeminiRequestError, "not saved"):
                GeminiNarrationService(GeminiSettings(api_key="test-key")).generate_story_response(self.packet())
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual(self.repository.get_state_value("currency.balance"), "20")
        self.assertFalse(self.repository.list_mechanical_events())

    def test_commit_rechecks_current_state_and_rolls_back_invalid_turn(self):
        before = self.repository.count_history()
        inventory_before = self.repository.list_inventory_items()
        with self.assertRaises(ContainerFlowError):
            StoryTurnService.commit_response(self.repository,
                AiNarrationResult("You pocket the coins.", suggested_events=broken_response()["events"]), message_id="bad-turn")
        self.assertEqual(self.repository.count_history(), before)
        self.assertEqual(self.repository.get_state_value("currency.balance"), "20")
        self.assertEqual(self.repository.list_inventory_items(), inventory_before)
        self.assertFalse(self.repository.list_mechanical_events())

    def test_category_container_cannot_bypass_the_transfer_guard(self):
        events = corrected_response()["events"]
        extra = broken_response()["events"][2]
        events.insert(2, extra)
        self.assertTrue(container_event_issues(events, self.packet()))
        results = EventApplier(self.repository).apply_events(events)
        self.assertEqual(results[2].status, "skipped")
        self.assertEqual(next(item for item in self.repository.list_inventory_items() if item["name"] == "Glass Vial")["quantity"], 1)

    def test_partial_transfer_cannot_duplicate_currency_and_remaining_items_transfer_once(self):
        EventApplier(self.repository).apply_events(corrected_response()["events"])
        take_map = {"type": "ContainerContentsTakenEvent", "payload": {
            "container_name": "Silver-Trimmed Satchel", "item_names": ["City Map"], "take_currency": False}}
        self.assertEqual(EventApplier(self.repository).apply_event(take_map).status, "applied")
        self.assertEqual(EventApplier(self.repository).apply_event(take_map).status, "skipped")
        self.assertEqual(self.repository.get_state_value("currency.balance"), "70")
        self.assertEqual(next(item for item in self.repository.list_inventory_items() if item["name"] == "City Map")["quantity"], 1)

    def test_initialized_contents_are_available_to_gm_but_cannot_be_replaced(self):
        EventApplier(self.repository).apply_event(corrected_response()["events"][0])
        # Close a test copy without changing the saved manifest.
        item = next(item for item in self.repository.list_inventory_items() if item["name"] == "Silver-Trimmed Satchel")
        metadata = item["metadata"]
        metadata["container"]["is_open"] = False
        self.repository.modify_inventory_item(target_name=item["name"], metadata=metadata)
        packet = self.packet()
        item_name = item["name"]
        visible = next(item for item in packet["state"]["inventory"]["items"] if item["name"] == item_name)
        self.assertNotIn("contents", visible["metadata"]["container"])
        self.assertIn("City Map", build_gemini_story_prompt(packet))
        forged = corrected_response()["events"][0]
        forged["payload"]["contents"]["currency_base_units"] = 999
        self.assertTrue(container_event_issues([forged], packet))
        self.assertEqual(EventApplier(self.repository).apply_event(forged).status, "skipped")

    def test_failed_lock_check_does_not_initialize_contents_or_transfer_rewards(self):
        item = next(item for item in self.repository.list_inventory_items() if item["name"] == "Silver-Trimmed Satchel")
        metadata = item["metadata"]
        metadata["container"].update(is_locked=True, lockpick_dc=18)
        self.repository.modify_inventory_item(target_name=item["name"], metadata=metadata)
        events = corrected_response()["events"]
        self.assertTrue(container_event_issues(events, self.packet()))
        results = EventApplier(self.repository).apply_events(events)
        self.assertEqual([result.status for result in results[:2]], ["skipped", "skipped"])
        self.assertEqual(self.repository.get_state_value("currency.balance"), "20")
        saved = next(item for item in self.repository.list_inventory_items() if item["name"] == "Silver-Trimmed Satchel")
        self.assertFalse(saved["metadata"]["container"]["contents_initialized"])


if __name__ == "__main__":
    unittest.main()
