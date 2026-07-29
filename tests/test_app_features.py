from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.features import (
    is_ai_enabled,
    is_playtesting_build,
    is_tts_enabled,
)


class AppFeatureTests(unittest.TestCase):
    def test_playtesting_mode_disables_ai_and_tts(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_ADVENTURE_PLAYTESTING_BUILD": "1"},
            clear=False,
        ):
            self.assertTrue(is_playtesting_build())
            self.assertFalse(is_ai_enabled())
            self.assertFalse(is_tts_enabled())

    def test_playtesting_mode_uses_isolated_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "APPDATA": temp_dir,
                    "LOCALAPPDATA": temp_dir,
                    "AI_ADVENTURE_PLAYTESTING_BUILD": "1",
                },
                clear=False,
            ):
                app_paths = AppPaths.create()

            self.assertEqual(
                app_paths.app_data_dir,
                Path(temp_dir) / "AI Adventure Playtesting",
            )
            self.assertTrue(app_paths.saves_dir.is_dir())
            self.assertEqual(
                app_paths.gemini_api_key_path,
                (
                    Path(temp_dir)
                    / "AI Adventure Playtesting"
                    / "gemini_api_key.txt"
                ).resolve(),
            )
            self.assertEqual(
                app_paths.gemini_terms_acceptance_path,
                (
                    Path(temp_dir)
                    / "AI Adventure Playtesting"
                    / "gemini_api_key_terms_acceptance.json"
                ).resolve(),
            )

    def test_playtesting_build_uses_minimal_dependencies_and_no_env_file(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        build_script = (project_root / "playtesting_build.bat").read_text(
            encoding="utf-8"
        )
        requirements = (
            project_root / "playtesting_requirements.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("AI Adventure Playtesting", build_script)
        self.assertIn("pyinstaller_playtesting_runtime.py", build_script)
        self.assertIn('--exclude-module "google"', build_script)
        self.assertNotIn('if not exist ".env"', build_script)
        self.assertEqual(
            requirements.splitlines(),
            ["PyInstaller==6.16.0", "PySide6==6.11.0"],
        )
