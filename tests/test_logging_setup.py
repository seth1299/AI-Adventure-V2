from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def test_log_file_uses_log_extension(self) -> None:
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

        self.assertEqual(app_paths.log_file.suffix, ".log")

    def test_configure_logging_replaces_existing_log_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "ai_adventure.log"
            log_file.write_text("old errors\n", encoding="utf-8")

            configure_logging(log_file)
            logging.info("fresh run")
            logging.shutdown()

            contents = log_file.read_text(encoding="utf-8")

            self.assertNotIn("old errors", contents)
            self.assertIn("fresh run", contents)


if __name__ == "__main__":
    unittest.main()
