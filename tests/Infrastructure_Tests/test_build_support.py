from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.app.build_support import prepare_dist_directory


class BuildSupportTests(unittest.TestCase):
    def test_prepare_dist_directory_does_not_copy_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source = project_root / ".env"
            source.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")

            destination = prepare_dist_directory(project_root)

            self.assertEqual(destination, project_root / "dist")
            self.assertFalse((destination / ".env").exists())

    def test_prepare_dist_directory_creates_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = prepare_dist_directory(Path(temp_dir))
            self.assertTrue(destination.is_dir())
