import tempfile
import unittest
from pathlib import Path

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.core.state_manager import StateManager


class TimedEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SaveRepository.create_new_save(Path(self.temp.name), "Growing garden")
        self.repo.set_travel_locations([{"name": "Cottage Garden", "location_scope": "specific"},
                                        {"name": "Other Garden", "location_scope": "specific"}])
        self.repo.set_state_value("location", "Cottage Garden")
        self.repo.set_current_calendar_minute(480)
        self.applier = EventApplier(self.repo)

    def upsert(self, **fields):
        return self.applier.apply_event({"type": "TimedEventUpsertedEvent", "payload": fields})

    def test_growth_clock_notes_reload_and_completion(self):
        result = self.upsert(name="Flax", location="Cottage Garden", quantity=3, quantity_unit="shoots",
                             current_state="Growing", next_state="Mature", duration_minutes=4320,
                             notes="Young green shoots.", is_estimate=True)
        self.assertEqual(result.status, "applied", result.message)
        event = self.repo.list_timed_events()[0]
        self.assertEqual(event["ready_at_minute"], 4800)
        self.repo.set_current_calendar_minute(1920)
        self.assertEqual(self.repo.list_timed_events()[0]["remaining_minutes"], 2880)
        updated = self.upsert(event_id=event["id"], notes="Soil loosened and compost added.")
        self.assertEqual(updated.status, "applied", updated.message)
        self.assertEqual(self.repo.list_timed_events()[0]["ready_at_minute"], 4800)
        reopened = SaveRepository(self.repo.db_path)
        self.assertIn("compost", reopened.list_timed_events()[0]["notes"])
        inventory = reopened.list_inventory_items()
        reopened.set_current_calendar_minute(4800)
        ready = reopened.list_timed_events()[0]
        self.assertEqual((ready["state"], ready["remaining_minutes"]), ("Mature", 0))
        self.assertEqual(reopened.list_inventory_items(), inventory)
        self.assertEqual(StateManager(reopened).load_state().to_dict()["timed_events"][0]["state"], "Mature")
        self.upsert(event_id=event["id"], current_state="Harvested", lifecycle="completed")
        self.assertEqual(self.repo.list_timed_events(), [])
        self.assertEqual(self.repo.list_timed_events(include_inactive=True)[0]["lifecycle"], "completed")

    def test_name_location_identity_unknown_quantities_and_renamed_location(self):
        self.upsert(name="Flax", location="Cottage Garden")
        first = self.repo.list_timed_events()[0]
        self.upsert(name="flax", location="Cottage Garden", notes="Healthy")
        self.assertEqual(self.repo.list_timed_events()[0]["id"], first["id"])
        self.upsert(name="Flax", location="Other Garden")
        self.assertEqual(len(self.repo.list_timed_events()), 2)
        self.assertEqual(first["quantity"], -1)
        self.assertIsNone(first["remaining_minutes"])
        locations = self.repo.get_travel_locations()
        locations[0]["name"] = "Renamed Garden"
        self.repo.set_travel_locations(locations)
        renamed = next(row for row in self.repo.list_timed_events() if row["id"] == first["id"])
        self.assertEqual(renamed["location"], "Renamed Garden")
        self.upsert(name="Flax", location="Renamed Garden", notes="Same bed")
        self.assertEqual(len(self.repo.list_timed_events()), 2)

    def test_snapshot_restore_and_transaction_rollback_include_processes(self):
        self.upsert(name="Flax", location="Cottage Garden", duration_minutes=300)
        self.repo.capture_message_snapshot("before-garden")
        self.upsert(name="Cotton", location="Cottage Garden", duration_minutes=600)
        self.assertTrue(self.repo.rollback_message("before-garden"))
        self.assertEqual([row["name"] for row in self.repo.list_timed_events()], ["Flax"])
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                self.repo.upsert_timed_event(name="Hemp", location="Cottage Garden")
                raise RuntimeError("rollback")
        self.assertEqual(len(self.repo.list_timed_events()), 1)

    def test_invalid_duration_does_not_create_a_timer(self):
        for duration in (-1, 1.5, True):
            result = self.upsert(name="Flax", location="Cottage Garden", duration_minutes=duration)
            self.assertEqual(result.status, "skipped")
        self.assertEqual(self.repo.list_timed_events(), [])

    def test_count_estimates_survive_updates_and_can_be_confirmed(self):
        result = self.upsert(name="Cotton", location="Cottage Garden", quantity=6, quantity_unit="plants",
                             quantity_is_estimate=True, is_estimate=False, duration_minutes=500,
                             notes="Assumed six plants for the small bed.")
        self.assertEqual(result.status, "applied", result.message)
        record = self.repo.list_timed_events()[0]
        self.upsert(event_id=record["id"], notes="Watered the bed.")
        reopened = SaveRepository(self.repo.db_path)
        persisted = reopened.list_timed_events()[0]
        self.assertEqual(persisted["quantity"], 6)
        self.assertIs(persisted["quantity_is_estimate"], True)
        self.assertIs(persisted["is_estimate"], False)
        self.upsert(event_id=record["id"], quantity=5, quantity_is_estimate=False)
        confirmed = reopened.list_timed_events()[0]
        self.assertEqual(confirmed["quantity"], 5)
        self.assertIs(confirmed["quantity_is_estimate"], False)
        self.assertEqual(confirmed["ready_at_minute"], record["ready_at_minute"])

    def test_invalid_count_estimate_flag_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "quantity_is_estimate"):
            self.repo.upsert_timed_event(name="Flax", location="Cottage Garden", quantity_is_estimate="yes")
        self.assertEqual(self.repo.list_timed_events(), [])
