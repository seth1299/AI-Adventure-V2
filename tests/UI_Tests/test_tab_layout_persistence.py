import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
from tests.qt_test_case import QtTestCase


class TabLayoutPersistenceTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repo = SaveRepository.create_new_save(self.directory, "Tab layout")
        self.shell = self.make_shell(self.repo)

    def make_shell(self, repo, *, smart=False):
        shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)
        shell.set_repository(repo, initially_hide_empty_tabs=smart, defer_music_playback=True, defer_visual_scan=True)
        def cleanup():
            shell.set_repository(None, defer_music_playback=True, defer_visual_scan=True)
            for window in shell._detached_windows.values():
                window.blockSignals(True)
                window.close()
            shell.close()
            shell.deleteLater()
        self.addCleanup(cleanup)
        return shell

    def keys(self, shell):
        return [str(shell.tab_bar.tabData(i)) for i in range(shell.tabs.count())]

    def test_timed_events_is_an_independent_main_tab_and_refreshes_with_the_clock(self):
        shell = self.shell
        index = shell._tab_index_for_key("timed_events")
        self.assertGreaterEqual(index, 0)
        self.assertIs(shell.tabs.widget(index), shell.timed_events_screen)
        self.assertEqual(shell.tabs.tabText(index), "Time-Sensitive Events")
        self.assertEqual([shell.calendar_screen.views.tabText(i)
                          for i in range(shell.calendar_screen.views.count())], ["Month", "Year Overview"])
        self.repo.set_current_calendar_minute(0)
        self.repo.set_travel_locations([{"name": "Garden", "location_scope": "specific"}])
        self.repo.upsert_timed_event(name="Flax", location="Garden", quantity=6, quantity_unit="shoots",
                                     quantity_is_estimate=True, ready_at_minute=60, is_estimate=False)
        shell.refresh_screens(scan_visual_assets=False)
        self.assertEqual(shell.timed_events_screen.tasks_table.item(0, 1).text(), "~6 shoots")
        self.assertEqual(shell.timed_events_screen.tasks_table.item(0, 4).text(), "1 hr")
        self.repo.set_current_calendar_minute(60)
        shell.refresh_screens(scan_visual_assets=False)
        self.assertEqual(shell.timed_events_screen.tasks_table.item(0, 4).text(), "Ready")
        self.assertIn("timed_events", self.repo.get_setting("ui.tab_layout")["known_tabs"])
        shell._close_tab(index)
        self.assertEqual(shell._tab_index_for_key("timed_events"), -1)
        shell._restore_tab("timed_events")
        self.assertGreaterEqual(shell._tab_index_for_key("timed_events"), 0)

    def test_moved_closed_restored_and_active_tabs_are_saved_immediately(self):
        shell = self.shell
        shell.tab_bar.moveTab(shell._tab_index_for_key("inventory"), 0)
        shell._close_tab(shell._tab_index_for_key("notes"))
        shell.tabs.setCurrentIndex(shell._tab_index_for_key("stats"))
        saved = SaveRepository(self.repo.db_path).get_setting("ui.tab_layout")
        self.assertEqual(saved["open_tabs"], self.keys(shell))
        self.assertEqual(saved["open_tabs"][0], "inventory")
        self.assertNotIn("notes", saved["open_tabs"])
        self.assertEqual(saved["active_tab"], "stats")
        fresh = self.make_shell(SaveRepository(self.repo.db_path))
        self.assertEqual(self.keys(fresh), saved["open_tabs"])
        self.assertEqual(fresh.tab_bar.tabData(fresh.tabs.currentIndex()), "stats")
        fresh._restore_tab("notes")
        self.assertIn("notes", SaveRepository(self.repo.db_path).get_setting("ui.tab_layout")["open_tabs"])

    def test_layout_survives_an_independent_python_process(self):
        self.shell._close_tab(self.shell._tab_index_for_key("notes"))
        self.shell.tab_bar.moveTab(self.shell._tab_index_for_key("stats"), 1)
        expected = self.keys(self.shell)
        script = """
import json, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
app = QApplication([])
shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)
shell.set_repository(SaveRepository(Path(sys.argv[1])), defer_music_playback=True, defer_visual_scan=True)
print(json.dumps([shell.tab_bar.tabData(i) for i in range(shell.tabs.count())]))
shell.set_repository(None, defer_music_playback=True, defer_visual_scan=True)
shell.close()
"""
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-c", script, str(self.repo.db_path)],
                                capture_output=True, text=True, env=env, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), expected)

    def test_main_menu_and_different_saves_do_not_share_layouts(self):
        self.shell._close_tab(self.shell._tab_index_for_key("notes"))
        old_keys = self.keys(self.shell)
        self.shell.set_repository(None, defer_visual_scan=True)
        other = SaveRepository.create_new_save(self.directory, "Other layout")
        self.shell.set_repository(other, defer_visual_scan=True)
        self.assertIn("notes", self.keys(self.shell))
        self.shell.set_repository(SaveRepository(self.repo.db_path), defer_visual_scan=True)
        self.assertEqual(self.keys(self.shell), old_keys)

    def test_unchanged_refreshes_and_invalid_operations_do_not_write(self):
        with patch.object(self.repo, "set_setting", wraps=self.repo.set_setting) as save:
            self.shell._persist_tab_layout()
            self.shell._close_tab(-1)
            self.shell._restore_tab("not_a_tab")
            self.shell._close_tab(self.shell._tab_index_for_key("conversation"))
            calls = [call for call in save.call_args_list if call.args[0] == "ui.tab_layout"]
            self.assertEqual(calls, [])
            self.shell._close_tab(self.shell._tab_index_for_key("notes"))
            calls = [call for call in save.call_args_list if call.args[0] == "ui.tab_layout"]
            self.assertEqual(len(calls), 1)

    def test_startup_rules_do_not_override_manual_choices_and_smart_hidden_tabs_still_reveal(self):
        self.shell.set_repository(None, defer_visual_scan=True)
        self.repo.set_setting("ui.tab_layout", None)
        smart = self.make_shell(self.repo, smart=True)
        self.assertIn("npcs", smart._smart_hidden_tabs)
        fresh = self.make_shell(SaveRepository(self.repo.db_path), smart=True)
        self.assertEqual(fresh._tab_index_for_key("npcs"), -1)
        self.repo.upsert_npc(name="Mara", npc_id="npc_mara")
        fresh._reveal_populated_smart_hidden_tabs()
        self.assertGreaterEqual(fresh._tab_index_for_key("npcs"), 0)
        fresh._close_tab(fresh._tab_index_for_key("npcs"))
        reopened = self.make_shell(SaveRepository(self.repo.db_path), smart=True)
        reopened._reveal_populated_smart_hidden_tabs()
        self.assertEqual(reopened._tab_index_for_key("npcs"), -1)

    def test_detached_tabs_remain_open_and_return_is_persisted(self):
        self.shell._detach_tab("notes", QPoint(100, 100))
        saved = self.repo.get_setting("ui.tab_layout")
        self.assertIn("notes", saved["detached_tabs"])
        fresh = self.make_shell(SaveRepository(self.repo.db_path))
        self.assertIn("notes", fresh._detached_windows)
        fresh._detached_windows["notes"].close()
        self.assertGreaterEqual(fresh._tab_index_for_key("notes"), 0)
        self.assertNotIn("notes", self.repo.get_setting("ui.tab_layout")["detached_tabs"])

    def test_story_regeneration_preserves_current_layout(self):
        self.repo.capture_message_snapshot("before_layout_change")
        self.shell._close_tab(self.shell._tab_index_for_key("notes"))
        saved = self.repo.get_setting("ui.tab_layout")
        self.assertTrue(self.repo.rollback_message("before_layout_change"))
        self.assertEqual(SaveRepository(self.repo.db_path).get_setting("ui.tab_layout"), saved)

    def test_unknown_duplicate_and_missing_protected_tabs_are_sanitized(self):
        self.repo.set_setting("ui.tab_layout", {"version": 1, "known_tabs": list(self.shell._tab_order),
            "open_tabs": ["notes", "notes", "unknown"], "active_tab": "unknown"})
        fresh = self.make_shell(SaveRepository(self.repo.db_path))
        self.assertEqual(self.keys(fresh).count("notes"), 1)
        self.assertNotIn("unknown", self.keys(fresh))
        protected = [key for key, spec in fresh._tab_specs.items() if not spec[2]]
        self.assertTrue(all(key in self.keys(fresh) for key in protected))


if __name__ == "__main__":
    unittest.main()
