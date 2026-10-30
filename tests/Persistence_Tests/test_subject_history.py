from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.ai.gemini_service import build_gemini_story_prompt
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class SubjectHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Subject recall")
        self.npc = self.repo.upsert_npc(name="Mara Vale", npc_id="npc_mara", location="Harbor")
        self.subject = {"type": "npc", "id": self.npc["id"]}

    def reply(self, text, events=None, cues=None):
        return SimpleNamespace(narrative_text=text, suggested_events=events or [], speaker_cues=cues or [],
                               sound_effect_cues=[], pronunciation_map={})

    def exchange(self, turn, player, story):
        StoryTurnService.record_player_action(self.repo, player, message_id=turn)
        StoryTurnService.commit_response(self.repo, self.reply(story), message_id=turn)

    def test_return_after_fifty_turns_recalls_exchange_without_duplicate_recent_history(self):
        self.exchange("old", "Ask Mara Vale about her brother.", "She says her brother sailed on the Nightjar.")
        for number in range(51):
            self.repo.append_history("player", f"Explore road {number}.")
            self.repo.append_history("story", f"The road continues past milestone {number}.")
        packet = StoryTurnService.build_context_packet(self.repo, "Ask Mara Vale what became of her brother.")
        entries = packet["subject_history"]["entries"]
        self.assertEqual(len(entries), 2)
        self.assertIn("Nightjar", entries[-1]["content"])
        self.assertFalse({row["id"] for row in entries} & {row["id"] for row in packet["recent_history"]})
        prompt = build_gemini_story_prompt(packet)
        self.assertIn("Nightjar", prompt)
        self.assertIn("current authoritative state takes precedence", prompt)
        self.assertNotIn("subject_history", StoryTurnService.build_context_packet(self.repo, "Inspect the road."))

    def test_new_npc_in_response_links_both_messages_and_survives_reload(self):
        StoryTurnService.record_player_action(self.repo, "Who are you?", message_id="intro")
        StoryTurnService.commit_response(self.repo, self.reply("A woman answers, 'I am Nella.'", [{
            "type": "NpcUpsertedEvent", "payload": {"npc_id": "npc_nella", "name": "Nella"},
        }]), message_id="intro")
        reloaded = SaveRepository(self.repo.db_path)
        entries = reloaded.list_history(kinds=("player", "story"))
        self.assertTrue(all({"type": "npc", "id": "npc_nella"} in entry["subjects"] for entry in entries))
        typed = StateManager(reloaded).load_state().history.entries[-1]
        self.assertEqual(typed.subjects, entries[-1]["subjects"])
        self.assertEqual(typed.turn_id, "intro")

    def test_subjects_from_speaker_cues_do_not_require_a_voice_assignment(self):
        StoryTurnService.record_player_action(self.repo, "What did you find?", message_id="speech")
        StoryTurnService.commit_response(self.repo, self.reply("'A map,' she says.", cues=[{
            "speaker_id": "npc_mara", "anchor_text": "A map", "speaker_name": "Mara Vale",
        }]), message_id="speech")
        self.assertTrue(all(self.subject in entry["subjects"] for entry in self.repo.list_history(kinds=("player", "story"))))

    def test_followup_inherits_subject_but_unrelated_action_does_not(self):
        self.exchange("chat", "Greet Mara Vale.", "She waves in reply.")
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, "Ask her about that."), [self.subject])
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, "Walk down the road."), [])
        StoryTurnService.record_player_action(self.repo, "Ask her about that.", message_id="followup")
        self.assertEqual(self.repo.list_history(limit=1)[0]["subjects"], [self.subject])

    def test_quoted_followup_keeps_partner_despite_location_mismatch_and_oog_interruption(self):
        self.exchange('chat', 'Greet Mara Vale.', 'Mara Vale offers to lend two books.')
        self.repo.set_state_value('location', 'City')  # NPC is at Harbor, like the Vanja incident.
        self.repo.append_history('player_oog', 'We should be at Harbor.')
        self.repo.append_history('story_oog', 'Noted.')
        command = '"Most excellent. Whcih am I able to check out, and when do you need them back by?"'
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, command), [self.subject])
        StoryTurnService.record_player_action(self.repo, command, message_id='followup')
        packet = StoryTurnService.build_context_packet(self.repo, command)
        self.assertEqual([npc['npc_id'] for npc in packet['state']['npcs']['relevant']], [self.npc['id']])
        self.assertIn('Mara Vale', build_gemini_story_prompt(packet))

    def test_item_reference_does_not_displace_conversation_partner(self):
        self.repo.add_inventory_item('Amber Locket', 'Valuable', 1, 'A small locket.')
        self.exchange('chat', 'Greet Mara Vale.', 'Mara Vale offers to inspect the Amber Locket.')
        subjects = StoryTurnService.history_subjects_for_action(self.repo, '"What do you know about the Amber Locket?"')
        self.assertIn(self.subject, subjects)
        self.assertTrue(any(subject['type'] == 'item' for subject in subjects))

    def test_naming_another_npc_and_new_scene_end_inheritance(self):
        other = self.repo.upsert_npc(npc_id='npc_bren', name='Bren', location='Market')
        self.exchange('chat', 'Greet Mara Vale.', 'Mara Vale waves.')
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, 'Ask Bren about work.'),
                         [{'type': 'npc', 'id': other['id']}])
        self.repo.append_history('story', 'Arrived at **Market** after 10 minutes.')
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, '"Hello there."'), [])

    def test_legacy_speaker_metadata_can_supply_partner_without_subject_links(self):
        self.repo.append_history('story', '"Welcome."',
                                 speaker_cues=[{'speaker_id': self.npc['id'], 'speaker_name': 'Mara Vale',
                                                'anchor_text': '"Welcome."'}])
        self.assertEqual(StoryTurnService.history_subjects_for_action(self.repo, 'Yes, please.'), [self.subject])

    def test_item_subjects_use_catalog_id_and_survive_inventory_removal(self):
        self.repo.add_inventory_item("Amber Locket", "Valuable", 1, "A small locket.")
        item = next(item for item in self.repo.list_item_catalog() if item["name"] == "Amber Locket")
        subject = {"type": "item", "id": item["id"]}
        self.exchange("locket", "Show Mara Vale the Amber Locket.", "She recognizes the engraved bird.")
        self.repo.remove_inventory_item("Amber Locket", 1)
        entries = self.repo.recall_subject_history([subject])
        self.assertEqual(len(entries), 2)
        self.assertIn("engraved bird", entries[-1]["content"])
        self.assertIn(subject, self.repo.find_history_subjects("Examine the Amber Locket."))

    def test_recall_is_bounded_chronological_deduplicated_and_filters_hidden_and_oog(self):
        for number in range(12):
            self.repo.append_history("story", f"Mara Vale recounts story {number}: " + "x" * 1500)
        self.repo.append_history("story_oog", "Secret test note", subjects=[self.subject])
        self.repo.append_history("story", "Mara Vale hidden reply")
        hidden = self.repo.list_history(limit=1)[0]
        self.repo.set_history_entry_hidden(hidden["id"], True)
        entries = self.repo.recall_subject_history([self.subject, self.subject], max_chars=2100)
        self.assertLessEqual(len(entries), 4)
        self.assertLessEqual(sum(len(entry["content"]) for entry in entries), 2100)
        self.assertTrue(all(len(entry["content"]) <= 1200 for entry in entries))
        ids = [entry["id"] for entry in entries]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertNotIn(hidden["id"], ids)
        self.assertTrue(all(entry["kind"] == "story" for entry in entries))

    def test_rollback_restores_subject_links_and_regeneration_does_not_retain_old_subjects(self):
        StoryTurnService.record_player_action(self.repo, "Look around.", message_id="redo")
        StoryTurnService.commit_response(self.repo, self.reply("Mara Vale waves."), message_id="redo")
        self.assertEqual(len(self.repo.recall_subject_history([self.subject])), 2)
        self.repo.rollback_message("redo")
        self.assertEqual(self.repo.recall_subject_history([self.subject]), [])
        StoryTurnService.commit_response(self.repo, self.reply("The square is empty."), message_id="redo")
        self.assertEqual(self.repo.recall_subject_history([self.subject]), [])

    def test_failed_transaction_does_not_leave_subject_links(self):
        before = self.repo.list_history()
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                self.repo.append_history("story", "Mara Vale waves.")
                raise RuntimeError("rollback")
        self.assertEqual(self.repo.list_history(), before)
        self.assertEqual(self.repo.recall_subject_history([self.subject]), [])

    def test_multiple_subjects_share_hard_global_limits(self):
        subjects = [self.subject, {"type": "npc", "id": "npc_two"}, {"type": "item", "id": "item_three"}]
        for subject in subjects:
            for number in range(8):
                self.repo.append_history("story", f"Long account {number}: " + "x" * 1800, subjects=[subject])
        entries = self.repo.recall_subject_history(subjects, per_subject=100, limit=100, max_chars=100000)
        self.assertLessEqual(len(entries), 6)
        self.assertEqual(sum(len(entry["content"]) for entry in entries), 6000)
        # Fair selection gives each requested subject a place in the prompt.
        for subject in subjects:
            subject_entries = {entry["id"] for entry in self.repo.recall_subject_history([subject])}
            self.assertTrue(subject_entries & {entry["id"] for entry in entries})

    def test_name_matching_uses_word_boundaries_and_skips_ambiguous_aliases(self):
        self.assertEqual(self.repo.find_history_subjects("Mara Valerian walks away."), [])
        self.repo.upsert_npc(name="Another Person", npc_id="npc_other", display_name="Mara Vale")
        self.assertEqual(self.repo.find_history_subjects("Ask Mara Vale."), [])
        self.assertEqual(self.repo.find_history_subjects("Ask npc_mara."), [self.subject])


if __name__ == "__main__":
    unittest.main()
