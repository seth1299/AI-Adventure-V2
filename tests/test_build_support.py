from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.app.build_support import copy_env_to_dist


class BuildSupportTests(unittest.TestCase):
    def test_copy_env_to_dist_preserves_the_source_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source = project_root / ".env"
            source.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")

            destination = copy_env_to_dist(project_root)

            self.assertEqual(destination, project_root / "dist" / ".env")
            self.assertEqual(destination.read_text(encoding="utf-8"), "GEMINI_API_KEY=test-key\n")

    def test_copy_env_to_dist_requires_a_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                copy_env_to_dist(Path(temp_dir))
