import json
from pathlib import Path
import tempfile
import unittest

from ai_adventure.ai.gemini_service import AiNarrationResult, _story_prompt_packet, build_story_response_schema
from ai_adventure.ai.gemini_service import D20TestPlanResult, _prefer_clearly_relevant_known_skill, _parse_new_game_starter_items
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.events.event_applier import AppliedEventResult
from ai_adventure.items import normalize_item_metadata
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.skills.books import SKILL_BOOK_RULE, reading_context


LINK = {"skill_name": "Dyeing", "attribute": "Intelligence",
        "description": "Preparing dyes, choosing mordants, and fixing colors to textile fibers."}
TITLE = "Wildwood Dyestuffs Folio"
STUDY = {"type": "SkillBookStudiedEvent", "payload": {"item_name": TITLE, "reason": "Studied mordant combinations for lasting colors."}}


class SkillBookStudyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Skill books")
        self.repo.add_inventory_item(TITLE, "Book", 1, "A reference manual of natural dyes and mordants.",
                                     metadata={"skill_book": LINK})
        self.repo.upsert_skill("Foraging", "Finding and gathering wild resources.", 1, attribute="Wisdom")

    def record(self, message="study", command=None):
        StoryTurnService.record_player_action(self.repo, command or f"I will read {TITLE} to learn its techniques.",
                                              message_id=message)

    def commit(self, events=None, message="study"):
        result = AiNarrationResult("You study the book's mordant techniques.", suggested_events=events if events is not None else [STUDY])
        return StoryTurnService.commit_response(self.repo, result, message_id=message)

    def test_link_survives_reload_catalog_updates_and_moving_book(self):
        self.repo.upsert_item_catalog_entry(name=TITLE, description="A well-thumbed reference.")
        reopened = SaveRepository(self.repo.db_path)
        item = next(item for item in reopened.list_inventory_items() if item["name"] == TITLE)
        self.assertEqual(item["metadata"]["skill_book"], LINK)
        catalog = next(item for item in reopened.list_item_catalog() if item["name"] == TITLE)
        self.assertEqual(catalog["metadata"]["skill_book"], LINK)
        self.assertIsNone(reopened.get_skill("Dyeing"))
        reopened.set_travel_locations([{"name": "Cottage", "location_scope": "specific"}])
        reopened.set_state_value("location", "Cottage")
        reopened.move_inventory_item(item["id"], "Cottage")
        reopened.modify_inventory_item(target_name=TITLE, new_name="Folio of Dyes")
        renamed = next(item for item in reopened.list_inventory_items() if item["name"] == "Folio of Dyes")
        self.assertEqual(renamed["metadata"]["skill_book"], LINK)

    def test_unknown_skill_is_learned_then_later_sessions_train_it(self):
        self.record()
        self.commit()
        skill = self.repo.get_skill("Dyeing")
        self.assertEqual((skill["level"], skill["xp"], skill["attribute"]), (1, 0, "Intelligence"))
        self.assertEqual(self.repo.get_skill("Foraging")["xp"], 0)
        self.record("study-again")
        self.commit(message="study-again")
        self.assertEqual(self.repo.get_skill("Dyeing")["xp"], 1)
        self.commit(message="study-again")
        self.assertEqual(self.repo.get_skill("Dyeing")["xp"], 1)

    def test_wrong_foraging_award_is_routed_to_book_subject_with_audit(self):
        self.record()
        result = self.commit([{"type": "SkillXpAddedEvent", "payload": {"skill_name": "Foraging", "xp_amount": 1}}])
        self.assertIsNotNone(self.repo.get_skill("Dyeing"))
        self.assertEqual(self.repo.get_skill("Foraging")["xp"], 0)
        audit = result.event_results[0]
        self.assertEqual(audit.event_type, "SkillBookStudiedEvent")
        self.assertEqual(audit.payload["original_training_events"][0]["payload"]["skill_name"], "Foraging")

    def test_learned_skill_cannot_gain_twice_in_same_message_even_different_event_reason(self):
        self.record()
        self.commit()
        applied = EventApplier(self.repo, message_id="study").apply_events([
            {"type": "SkillBookStudiedEvent", "payload": {**STUDY["payload"], "reason": "Another description of the same session."}}
        ])
        self.assertEqual(applied[0].status, "skipped")
        self.assertEqual(self.repo.get_skill("Dyeing")["xp"], 0)

    def test_opening_or_skimming_does_not_authorize_study(self):
        for command in (f"I open {TITLE}.", f"I skim the cover of {TITLE}.", f"I will not read {TITLE}."):
            self.assertEqual(reading_context(self.repo.list_accessible_inventory_items(), command), {})

    def test_inaccessible_book_cannot_teach_and_turn_rolls_back(self):
        self.repo.set_travel_locations([{"name": "Remote Library", "location_scope": "specific"}])
        self.repo.set_state_value("location", "Remote Library")
        self.repo.modify_inventory_item(target_name=TITLE, metadata={"storage_location": "Remote Library"})
        self.repo.set_state_value("location", "Elsewhere")
        self.record()
        before = self.repo.list_history()
        with self.assertRaisesRegex(ValueError, "Book study"):
            self.commit()
        self.assertIsNone(self.repo.get_skill("Dyeing"))
        self.assertEqual(self.repo.list_history(), before)

    def test_link_can_be_defined_for_legacy_book_before_its_first_study(self):
        with self.repo.transaction() as connection:
            row = connection.execute("SELECT id,metadata_json FROM inventory_items WHERE name=?", (TITLE,)).fetchone()
            metadata = json.loads(row["metadata_json"])
            del metadata["skill_book"]
            connection.execute("UPDATE inventory_items SET metadata_json=? WHERE id=?", (json.dumps(metadata), row["id"]))
        self.record()
        link_event = {"type": "InventoryItemModifiedEvent", "payload": {"target_name": TITLE, "skill_book": LINK}}
        self.commit([link_event, {"type": "SkillXpAddedEvent", "payload": {"skill_name": "Foraging", "xp_amount": 1}}])
        self.assertEqual(self.repo.get_skill("Dyeing")["level"], 1)

    def test_established_book_subject_cannot_silently_change(self):
        result = EventApplier(self.repo).apply_event({"type": "InventoryItemModifiedEvent",
                    "payload": {"target_name": TITLE, "skill_book": {**LINK, "skill_name": "Foraging"}}})
        self.assertEqual(result.status, "skipped")

    def test_master_cap_and_out_of_game_remain_respected(self):
        self.repo.upsert_skill("Dyeing", LINK["description"], 5, attribute="Intelligence")
        self.record()
        self.commit()
        self.assertEqual(self.repo.get_skill("Dyeing")["xp"], 32)
        StoryTurnService.commit_response(self.repo, AiNarrationResult("A discussion.", suggested_events=[STUDY]),
                                         message_id="discussion", conversation_mode="out_of_game")
        self.assertEqual(self.repo.get_skill("Dyeing")["xp"], 32)

    def test_reading_packet_and_schema_include_book_link_and_learning_events(self):
        packet = StoryTurnService.build_context_packet(self.repo, f"Read {TITLE}.")
        packet["selection"]["tags"] = []
        projected = _story_prompt_packet(packet)
        self.assertEqual(projected["state"]["skill_book_study"]["skill_book"], LINK)
        self.assertEqual(projected["response_contract"]["skill_books"], SKILL_BOOK_RULE)
        schema = build_story_response_schema(packet, for_api=False)
        branches = schema["properties"]["events"]["items"]["anyOf"]
        kinds = {branch["properties"]["type"]["enum"][0] for branch in branches}
        self.assertTrue({"SkillBookStudiedEvent", "SkillUpsertedEvent", "SkillXpAddedEvent", "InventoryItemModifiedEvent"} <= kinds)

    def test_invalid_link_does_not_silently_default_to_another_skill(self):
        with self.assertRaises(ValueError):
            normalize_item_metadata({"skill_book": {"skill_name": "Dyeing"}})

    def test_planner_does_not_use_foraging_to_understand_a_dyeing_book(self):
        packet = StoryTurnService.build_context_packet(self.repo, f"Read {TITLE}.")
        plan = D20TestPlanResult(checks=[{"attribute": "Wisdom", "skill_name": "Foraging",
                                        "reason": "Understand the book's instructions", "test_kind": "check"}])
        corrected = _prefer_clearly_relevant_known_skill(plan, packet)
        self.assertNotIn("skill_name", corrected.checks[0])
        self.assertEqual(corrected.checks[0]["attribute"], "Intelligence")

    def test_new_game_book_metadata_survives_parser_and_save_without_teaching(self):
        items = _parse_new_game_starter_items([{"name": TITLE, "category": "Book", "quantity": 1,
                                               "description": "A dye manual.", "skill_book": LINK}])
        self.repo.replace_inventory_items(items)
        book = next(item for item in self.repo.list_inventory_items() if item["name"] == TITLE)
        self.assertEqual(book["metadata"]["skill_book"], LINK)
        self.assertIsNone(self.repo.get_skill("Dyeing"))

    def test_link_definition_and_learning_survive_failed_attribute_check(self):
        with self.repo.transaction() as connection:
            connection.execute("UPDATE inventory_items SET metadata_json=json_remove(metadata_json, '$.skill_book') WHERE name=?", (TITLE,))
        self.record()
        link = {"type": "InventoryItemModifiedEvent", "payload": {"target_name": TITLE, "skill_book": LINK}}
        StoryTurnService.commit_response(self.repo, AiNarrationResult("You learn from the book despite struggling.",
            suggested_events=[link, STUDY]), message_id="study",
            prior_event_results=[AppliedEventResult("D20TestRequestedEvent", "applied", "Failed.", {"outcome": "failure"})])
        self.assertIsNotNone(self.repo.get_skill("Dyeing"))

    def test_mixed_actions_do_not_redirect_unrelated_skill_awards(self):
        from ai_adventure.skills.books import normalize_book_training
        events = [STUDY, {"type": "D20TestRequestedEvent", "payload": {"reason": "Gather herbs"}},
                  {"type": "SkillXpAddedEvent", "payload": {"skill_name": "Foraging", "xp_amount": 1}}]
        self.assertEqual(normalize_book_training(events, self.repo.list_accessible_inventory_items(),
                                                 f"Read {TITLE}, then gather herbs."), events)
        practice = [events[-1]]
        self.assertEqual(normalize_book_training(practice, self.repo.list_accessible_inventory_items(),
                                                 f"Read {TITLE} and practice Foraging."), practice)
