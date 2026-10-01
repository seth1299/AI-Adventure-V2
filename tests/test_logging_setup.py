from __future__ import annotations

import logging
import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def test_log_file_uses_json_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_appdata = os.environ.get("APPDATA")
            os.environ["APPDATA"] = temp_dir

            try:
                app_paths = AppPaths.create()
            finally:
                if old_appdata is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old_appdata

        self.assertEqual(app_paths.log_file.suffix, ".json")

    def test_configure_logging_replaces_existing_log_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "ai_adventure.json"
            log_file.write_text("old errors\n", encoding="utf-8")

            configure_logging(log_file)
            logging.info("fresh run")
            logging.shutdown()

            contents = log_file.read_text(encoding="utf-8")

            self.assertNotIn("old errors", contents)
            self.assertIn("fresh run", contents)

    def test_log_records_are_human_readable_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "ai_adventure.json"

            configure_logging(log_file)
            logging.getLogger("ai_adventure.ui.themes_audio").info(
                "TTS narration disabled by application configuration."
            )
            logging.shutdown()

            document = json.loads(log_file.read_text(encoding="utf-8"))
            records = document["messages"]

            self.assertGreaterEqual(len(records), 2)
            configured = records[0]
            self.assertRegex(configured["timestamp"], r"^\d{1,2}-\d{1,2}-\d{2}, \d{1,2}:\d{2} (?:A|P)\.M\.$")
            self.assertRegex(configured["message_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(configured["file_location"], "root")
            self.assertEqual(configured["log_type"], "INFO")

            message = records[-1]
            self.assertEqual(message["file_location"], "ai_adventure.ui.themes_audio")
            self.assertEqual(message["log_type"], "INFO")
            self.assertEqual(
                message["log_message"],
                "TTS narration disabled by application configuration.",
            )
            self.assertEqual(len({record["message_id"] for record in records}), len(records))


if __name__ == "__main__":
    unittest.main()
