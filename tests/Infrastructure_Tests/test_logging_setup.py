from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.logging_setup import ApplicationFileHandler, configure_logging


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.old_level = self.root.level
        self.environment = patch.dict(os.environ, {"AI_ADVENTURE_PLAYTESTING_BUILD": "0"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.root.setLevel, self.old_level)
        self.addCleanup(self.close_log)

    def close_log(self):
        for handler in self.root.handlers[:]:
            if isinstance(handler, ApplicationFileHandler):
                self.root.removeHandler(handler)
                handler.close()

    def test_log_file_uses_log_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"APPDATA": directory}):
                self.assertEqual(AppPaths.create().log_file.suffix, ".log")

    def test_records_append_with_labeled_lines_and_one_blank_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_adventure.log"
            path.write_text("old errors", encoding="utf-8")
            configure_logging(path)
            first = path.read_text(encoding="utf-8")
            logging.getLogger("ai_adventure.test").info("fresh run")
            contents = path.read_text(encoding="utf-8")
            self.assertTrue(contents.startswith(first))
            self.assertNotIn("old errors", contents)
            records = contents.rstrip().split("\n\n")
            self.assertEqual(len(records), 2)
            for record in records:
                self.assertRegex(record, r"Severity: INFO\nFile: .*\nFunction: .*\nDate/Time: .*\nMessage ID: [0-9a-f]{32}\nMessage: ")
            self.assertIn("Function: test_records_append_with_labeled_lines_and_one_blank_separator", records[1])
            self.assertTrue(contents.endswith("\n\n"))
            self.close_log()

    def test_reconfiguration_preserves_current_session_and_unrelated_handlers(self):
        unrelated = logging.NullHandler()
        self.root.addHandler(unrelated)
        self.addCleanup(self.root.removeHandler, unrelated)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_adventure.log"
            configure_logging(path)
            logging.info("first message")
            configure_logging(path)
            logging.info("second message")
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count("Message: first message"), 1)
            self.assertEqual(contents.count("Message: second message"), 1)
            self.assertIn(unrelated, self.root.handlers)
            self.close_log()

    def test_new_process_starts_fresh_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_adventure.log"
            script = "from pathlib import Path; import sys,logging; from ai_adventure.app.logging_setup import configure_logging; configure_logging(Path(sys.argv[1])); logging.info(sys.argv[2]); logging.shutdown()"
            for message in ("previous session", "current session"):
                subprocess.run([sys.executable, "-c", script, str(path), message], check=True, capture_output=True, timeout=15)
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("previous session", contents)
            self.assertIn("current session", contents)

    def test_debug_only_in_playtesting_and_exceptions_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_adventure.log"
            configure_logging(path)
            logging.debug("normal debug should be absent")
            try:
                raise ValueError("failure details")
            except ValueError:
                logging.exception("operation failed")
            with patch.dict(os.environ, {"AI_ADVENTURE_PLAYTESTING_BUILD": "1"}):
                configure_logging(path)
                logging.debug("playtesting debug")
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("normal debug should be absent", contents)
            self.assertIn("Severity: ERROR", contents)
            self.assertIn("Exception:\nTraceback", contents)
            self.assertIn("ValueError: failure details", contents)
            self.assertIn("Severity: DEBUG", contents)
            self.assertIn("playtesting debug", contents)
            self.close_log()

    def test_raw_gemini_response_logging_is_build_gated(self):
        from ai_adventure.ai.gemini_service import GeminiNarrationService, GeminiSettings
        from google import genai
        from types import SimpleNamespace
        import json
        raw = json.dumps({"response": "The messenger arrives.", "suggested_actions": [], "events": [], "out_of_game": True})
        with tempfile.TemporaryDirectory() as directory:
            for build in ("0", "1"):
                path = Path(directory) / (build + ".log")
                with patch.dict(os.environ, {"AI_ADVENTURE_PLAYTESTING_BUILD": build}):
                    configure_logging(path)
                    with patch.object(genai, "Client"), patch(
                        "ai_adventure.ai.gemini_service._generate_content_with_retry",
                        return_value=SimpleNamespace(text=raw),
                    ), patch("ai_adventure.ai.gemini_service._repair_gemini_creative_terms", return_value=raw):
                        GeminiNarrationService(GeminiSettings(api_key="test-key", model="gemini-2.5-flash")).generate_story_response(
                            {"conversation_mode": "out_of_game", "player_command": "Ask a question"}
                        )
                contents = path.read_text(encoding="utf-8")
                self.assertEqual("Gemini raw story response:" in contents, build == "1")
                self.assertNotIn("test-key", contents)
                self.close_log()


if __name__ == "__main__":
    unittest.main()
