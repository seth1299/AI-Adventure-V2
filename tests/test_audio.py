from __future__ import annotations

import os
import queue
import sys
import threading
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.audio.narration import (
    GeneratedNarrationChunk,
    NarrationPlayer,
    build_narration_chunks,
    chunk_tts_text,
    normalize_tts_time_text,
    sanitize_tts_text,
)
from ai_adventure.audio.ssmd import strip_ssmd_markup_for_plain_tts
from ai_adventure.audio.tts_settings import (
    build_voice_blend_spec,
    merge_custom_voices,
    normalize_custom_voices,
    normalize_narrator_voice_spec,
    parse_voice_blend_spec,
)
from ai_adventure.audio.sound_manager import (
    SOUND_EFFECT_CHANNEL_INDEX,
    SoundManager,
    prepare_sound_directory,
    prepare_sound_effect_directory,
)
from ai_adventure.audio.tts import tts_manager
from ai_adventure.audio.tts.tts_manager import (
    KokoroOnnxTTSEngine,
    PyKokoroTTSEngine,
    TTSRequest,
)


class AudioTests(unittest.TestCase):
    def test_music_loops_while_sound_effect_plays_once(self) -> None:
        class FakeMusic:
            def __init__(self) -> None:
                self.stop_calls = 0
                self.loaded = ""
                self.play_loops: list[int] = []

            def get_busy(self) -> bool:
                return bool(self.play_loops)

            def stop(self) -> None:
                self.stop_calls += 1

            def load(self, path: str) -> None:
                self.loaded = path

            def set_volume(self, _volume: float) -> None:
                pass

            def play(self, loops: int) -> None:
                self.play_loops.append(loops)

        class FakeChannel:
            def __init__(self) -> None:
                self.stop_calls = 0
                self.played: list[tuple[object, int]] = []

            def get_busy(self) -> bool:
                return bool(self.played)

            def stop(self) -> None:
                self.stop_calls += 1

            def set_volume(self, _volume: float) -> None:
                pass

            def play(self, sound: object, *, loops: int = 0) -> None:
                self.played.append((sound, loops))

        fake_music = FakeMusic()
        fake_channels: dict[int, FakeChannel] = {}

        class FakeMixer:
            music = fake_music

            @staticmethod
            def get_init() -> bool:
                return True

            @staticmethod
            def Channel(index: int) -> FakeChannel:
                return fake_channels.setdefault(index, FakeChannel())

            @staticmethod
            def Sound(path: str) -> object:
                return types.SimpleNamespace(path=path)

        fake_pygame = types.ModuleType("pygame")
        setattr(fake_pygame, "mixer", FakeMixer)
        original_pygame = sys.modules.get("pygame")

        try:
            sys.modules["pygame"] = fake_pygame
            with TemporaryDirectory() as temp_dir:
                music_dir = Path(temp_dir) / "music"
                effect_dir = Path(temp_dir) / "effects"
                music_dir.mkdir()
                effect_dir.mkdir()
                music_path = music_dir / "Slow Jazz.mp3"
                effect_path = effect_dir / "Steady Rain.wav"
                music_path.write_bytes(b"music")
                effect_path.write_bytes(b"rain")
                (effect_dir / music_path.name).write_bytes(b"misfiled music")
                manager = SoundManager(music_dir, effect_dir)
                self.assertEqual(manager.get_valid_track_names(), [music_path.name])
                self.assertEqual(
                    manager.get_valid_sound_effect_names(),
                    [effect_path.name],
                )
                manager.play_music(music_path.name)
                music_stop_calls = fake_music.stop_calls
                manager.play_sound_effect(effect_path.name)

                self.assertEqual(manager.current_music, music_path.name)
                self.assertEqual(fake_music.stop_calls, music_stop_calls)
                self.assertEqual(fake_music.play_loops, [-1])
                self.assertEqual(
                    fake_channels[SOUND_EFFECT_CHANNEL_INDEX].played[0][1],
                    0,
                )
        finally:
            if original_pygame is None:
                sys.modules.pop("pygame", None)
            else:
                sys.modules["pygame"] = original_pygame

    def test_sanitize_tts_text_removes_embedded_events_and_action_suggestions(self) -> None:
        text = sanitize_tts_text(
            "The room falls quiet. "
            "[" "["
            "MUSIC: Boss_Fight.mp3"
            "]" "]"
            "\n\n"
            "- Search the desk.\n"
            "- Leave the room."
        )

        self.assertEqual(text, "The room falls quiet.")

    def test_chunk_tts_text_splits_long_narration(self) -> None:
        chunks = chunk_tts_text(
            "First sentence. Second sentence. Third sentence.",
            max_length=25,
        )

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0], "First sentence. Second")

    def test_sanitize_tts_text_converts_clock_times_for_speech(self) -> None:
        text = sanitize_tts_text(
            "The bell rings at 7:00 A.M. The gates close at 18:05."
        )

        self.assertIn("[7:00 A.M.](as: time)", text)
        self.assertIn("[18:05](as: time)", text)

        plain_text = strip_ssmd_markup_for_plain_tts(text)

        self.assertIn("seven in the morning", plain_text)
        self.assertIn("six oh five in the evening", plain_text)

    def test_normalize_tts_time_text_handles_midnight_noon_and_minutes(self) -> None:
        text = normalize_tts_time_text(
            "Meet at 12:00 A.M., return by 12:00 P.M., and report at 8:30 P.M."
        )

        self.assertIn("midnight", text)
        self.assertIn("noon", text)
        self.assertIn("eight thirty in the evening", text)

    def test_plain_tts_strips_native_phoneme_annotations_to_visible_text(self) -> None:
        self.assertEqual(
            strip_ssmd_markup_for_plain_tts(
                'The [Qh’thala]{ph="kəˈθɑlə"} waits.'
            ),
            "The Qh’thala waits.",
        )

    def test_narration_chunks_keep_display_text_separate_from_tts_text(self) -> None:
        chunks = build_narration_chunks("The bell rings at 7:00 A.M. What do you do now?")

        self.assertEqual(
            chunks[0].display_text,
            "The bell rings at 7:00 A.M. What do you do now?",
        )
        self.assertEqual(
            chunks[0].tts_text,
            "The bell rings at [7:00 A.M.](as: time) What do you do now?",
        )

    def test_narration_chunks_apply_pronunciation_only_to_spoken_text(self) -> None:
        text = "Ironpeak City wakes.\n\nThe market opens."
        chunks = build_narration_chunks(
            text,
            tts_text_transform=lambda chunk: chunk.replace(
                "Ironpeak City",
                '[Ironpeak City]{ph="ˈaɪɚnˌpik ˈsɪti"}',
            ),
        )

        self.assertEqual("".join(chunk.display_text for chunk in chunks), text)
        self.assertEqual(chunks[0].display_text, "Ironpeak City wakes.\n\n")
        self.assertIn('[Ironpeak City]{ph="ˈaɪɚnˌpik ˈsɪti"}', chunks[0].tts_text)
        self.assertNotIn("ˈaɪɚnˌpik", chunks[1].tts_text)

    def test_kokoro_onnx_uses_native_phoneme_mode_for_ipa_overrides(self) -> None:
        create_calls: list[tuple[str, dict[str, object]]] = []

        class FakeTokenizer:
            @staticmethod
            def phonemize(text: str, _language: str) -> str:
                return f"<{text}>"

        class FakeKokoro:
            def __init__(self, _model_path: str, _voices_path: str) -> None:
                self.tokenizer = FakeTokenizer()

            def create(self, text: str, **kwargs: object):
                create_calls.append((text, kwargs))
                return [0.0], 24000

        writes: list[tuple[str, object, int]] = []
        fake_kokoro_module = types.ModuleType("kokoro_onnx")
        setattr(fake_kokoro_module, "Kokoro", FakeKokoro)
        fake_soundfile = types.ModuleType("soundfile")
        setattr(
            fake_soundfile,
            "write",
            lambda path, samples, rate: writes.append((path, samples, rate)),
        )
        original_kokoro = sys.modules.get("kokoro_onnx")
        original_soundfile = sys.modules.get("soundfile")

        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "model.onnx"
            voices_path = Path(temp_dir) / "voices.bin"
            model_path.touch()
            voices_path.touch()
            try:
                sys.modules["kokoro_onnx"] = fake_kokoro_module
                sys.modules["soundfile"] = fake_soundfile
                engine = KokoroOnnxTTSEngine(
                    model_path=model_path,
                    voices_path=voices_path,
                    output_directory=temp_dir,
                )
                engine.synthesize_to_file(
                    TTSRequest(
                        text='The [Qh’thala]{ph="kəˈθɑlə"} waits.',
                        voice="af_sarah",
                    )
                )
            finally:
                if original_kokoro is None:
                    sys.modules.pop("kokoro_onnx", None)
                else:
                    sys.modules["kokoro_onnx"] = original_kokoro
                if original_soundfile is None:
                    sys.modules.pop("soundfile", None)
                else:
                    sys.modules["soundfile"] = original_soundfile

        self.assertEqual(create_calls[0][0], "<The> kəˈθɑlə <waits.>")
        self.assertTrue(create_calls[0][1]["is_phonemes"])
        self.assertEqual(len(writes), 1)

    def test_narration_sound_cue_forces_exact_word_boundary(self) -> None:
        text = "The hammer falls. Sparks leap from the anvil."
        chunks = build_narration_chunks(
            text,
            sound_effect_cues=[
                {
                    "filename": "Hammer Strike.wav",
                    "anchor_text": "hammer",
                    "position": "after",
                }
            ],
        )

        self.assertEqual("".join(chunk.display_text for chunk in chunks), text)
        self.assertEqual(chunks[0].display_text, "The hammer")
        self.assertEqual(chunks[0].sound_effects_after, ("Hammer Strike.wav",))
        self.assertEqual(chunks[1].display_text, " falls. Sparks leap from the anvil.")

    def test_narration_player_fires_cue_after_anchored_audio_chunk(self) -> None:
        player = NarrationPlayer.__new__(NarrationPlayer)
        player.enabled = True
        player._session_id = 7
        player._state_lock = threading.Lock()
        audio_queue: queue.Queue[GeneratedNarrationChunk | None] = queue.Queue()
        audio_queue.put(
            GeneratedNarrationChunk(
                audio_path=Path("first.wav"),
                display_text="The hammer",
                sound_effects_after=("Hammer Strike.wav",),
            )
        )
        audio_queue.put(
            GeneratedNarrationChunk(
                audio_path=Path("second.wav"),
                display_text=" falls.",
            )
        )
        audio_queue.put(None)
        order: list[str] = []

        with patch.object(
            player,
            "_play_file_blocking",
            side_effect=lambda path, _session_id: order.append(f"speak:{path.name}"),
        ):
            player._play_chunks(
                7,
                audio_queue,
                lambda chunk: order.append(f"chunk:{chunk}"),
                lambda filename: order.append(f"effect:{filename}"),
                None,
            )

        self.assertEqual(
            order,
            [
                "chunk:The hammer",
                "speak:first.wav",
                "effect:Hammer Strike.wav",
                "chunk: falls.",
                "speak:second.wav",
            ],
        )

    def test_voice_blend_specs_round_trip_for_tts_engines(self) -> None:
        blend = parse_voice_blend_spec("af_sarah:65,am_echo:35")

        assert blend is not None

        self.assertEqual(blend["voice_a"], "af_sarah")
        self.assertEqual(blend["voice_b"], "am_echo")
        self.assertEqual(blend["voice_a_weight"], 65)
        self.assertEqual(build_voice_blend_spec(blend), "af_sarah:65,am_echo:35")
        self.assertEqual(
            normalize_narrator_voice_spec("af_sarah:65,am_echo:35"),
            "af_sarah:65,am_echo:35",
        )

    def test_custom_voice_normalization_preserves_volume_and_speed(self) -> None:
        voices = normalize_custom_voices(
            [
                {
                    "name": "Storm Blend",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 70,
                    "tts_volume": 42,
                    "tts_speed": 135,
                }
            ]
        )

        self.assertEqual(voices[0]["name"], "Storm Blend")
        self.assertEqual(voices[0]["voice_a"], "af_sarah")
        self.assertEqual(voices[0]["voice_a_weight"], 70)
        self.assertEqual(voices[0]["voice_b_weight"], 30)
        self.assertEqual(voices[0]["tts_volume"], 42)
        self.assertEqual(voices[0]["tts_speed"], 135)

    def test_merge_custom_voices_preserves_first_matching_name(self) -> None:
        voices = merge_custom_voices(
            [
                {
                    "name": "Storm Blend",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 70,
                }
            ],
            [
                {
                    "name": "Storm Blend",
                    "voice_a": "am_echo",
                    "voice_b": "af_sarah",
                    "voice_a_weight": 20,
                },
                {
                    "name": "Rain Blend",
                    "voice_a": "am_echo",
                    "voice_b": "af_sarah",
                    "voice_a_weight": 55,
                },
            ],
        )

        self.assertEqual([voice["name"] for voice in voices], ["Storm Blend", "Rain Blend"])
        self.assertEqual(voices[0]["voice_a_weight"], 70)

    def test_pykokoro_engine_forces_q8_model_quality(self) -> None:
        captured_configs = []
        loaded_spacy_models = []

        class FakePipelineConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                captured_configs.append(kwargs)

        class FakeGenerationConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakePipeline:
            def __init__(self, config):
                self.config = config

        class FakeVoiceBlend:
            @staticmethod
            def parse(spec):
                return spec

        class FakeTokenizerConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_spacy = types.ModuleType("spacy")
        setattr(fake_spacy, "load", loaded_spacy_models.append)
        fake_pykokoro = types.ModuleType("pykokoro")
        setattr(fake_pykokoro, "KokoroPipeline", FakePipeline)
        setattr(fake_pykokoro, "PipelineConfig", FakePipelineConfig)
        fake_generation_config = types.ModuleType("pykokoro.generation_config")
        setattr(fake_generation_config, "GenerationConfig", FakeGenerationConfig)
        fake_tokenizer = types.ModuleType("pykokoro.tokenizer")
        setattr(fake_tokenizer, "TokenizerConfig", FakeTokenizerConfig)
        fake_voice_manager = types.ModuleType("pykokoro.voice_manager")
        setattr(fake_voice_manager, "VoiceBlend", FakeVoiceBlend)
        original_modules = {
            name: sys.modules.get(name)
            for name in (
                "spacy",
                "pykokoro",
                "pykokoro.generation_config",
                "pykokoro.tokenizer",
                "pykokoro.voice_manager",
            )
        }

        try:
            sys.modules["spacy"] = fake_spacy
            sys.modules["pykokoro"] = fake_pykokoro
            sys.modules["pykokoro.generation_config"] = fake_generation_config
            sys.modules["pykokoro.tokenizer"] = fake_tokenizer
            sys.modules["pykokoro.voice_manager"] = fake_voice_manager
            engine = PyKokoroTTSEngine()
            engine._pipeline_for("af_sarah", 1.0, "en-us")

            self.assertEqual(loaded_spacy_models, ["en_core_web_sm"])
            self.assertEqual(engine.model_quality, "q8")
            self.assertEqual(captured_configs[0]["model_quality"], "q8")
            self.assertEqual(
                captured_configs[0]["tokenizer_config"].kwargs["spacy_model"],
                "en_core_web_sm",
            )
            self.assertEqual(
                captured_configs[0]["tokenizer_config"].kwargs["spacy_model_size"],
                "sm",
            )
        finally:
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_pykokoro_engine_uses_packaged_spacy_model_path_when_name_missing(self) -> None:
        captured_configs = []
        loaded_spacy_models = []

        class FakePipelineConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                captured_configs.append(kwargs)

        class FakeGenerationConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakePipeline:
            def __init__(self, config):
                self.config = config

        class FakeVoiceBlend:
            @staticmethod
            def parse(spec):
                return spec

        class FakeTokenizerConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "en_core_web_sm"
            model_dir.mkdir()
            model_file = model_dir / "__init__.py"
            model_file.write_text("", encoding="utf-8")

            def fake_spacy_load(model):
                loaded_spacy_models.append(model)
                if model == "en_core_web_sm":
                    raise OSError("missing model")
                return object()

            fake_spacy = types.ModuleType("spacy")
            setattr(fake_spacy, "load", fake_spacy_load)
            fake_model = types.ModuleType("en_core_web_sm")
            setattr(fake_model, "__file__", str(model_file))
            fake_pykokoro = types.ModuleType("pykokoro")
            setattr(fake_pykokoro, "KokoroPipeline", FakePipeline)
            setattr(fake_pykokoro, "PipelineConfig", FakePipelineConfig)
            fake_generation_config = types.ModuleType("pykokoro.generation_config")
            setattr(fake_generation_config, "GenerationConfig", FakeGenerationConfig)
            fake_tokenizer = types.ModuleType("pykokoro.tokenizer")
            setattr(fake_tokenizer, "TokenizerConfig", FakeTokenizerConfig)
            fake_voice_manager = types.ModuleType("pykokoro.voice_manager")
            setattr(fake_voice_manager, "VoiceBlend", FakeVoiceBlend)
            original_modules = {
                name: sys.modules.get(name)
                for name in (
                    "spacy",
                    "en_core_web_sm",
                    "pykokoro",
                    "pykokoro.generation_config",
                    "pykokoro.tokenizer",
                    "pykokoro.voice_manager",
                )
            }

            try:
                sys.modules["spacy"] = fake_spacy
                sys.modules["en_core_web_sm"] = fake_model
                sys.modules["pykokoro"] = fake_pykokoro
                sys.modules["pykokoro.generation_config"] = fake_generation_config
                sys.modules["pykokoro.tokenizer"] = fake_tokenizer
                sys.modules["pykokoro.voice_manager"] = fake_voice_manager
                engine = PyKokoroTTSEngine()
                engine._pipeline_for("af_sarah", 1.0, "en-us")

                self.assertEqual(loaded_spacy_models, ["en_core_web_sm", model_dir])
                self.assertEqual(
                    captured_configs[0]["tokenizer_config"].kwargs["spacy_model"],
                    str(model_dir),
                )
            finally:
                for name, module in original_modules.items():
                    if module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module

    def test_preferred_tts_engine_defaults_to_kokoro_onnx(self) -> None:
        calls = []

        class FakeOnnxEngine:
            def __init__(self, **kwargs):
                calls.append(("onnx", kwargs))

        class FakePyKokoroEngine:
            def __init__(self, **kwargs):
                calls.append(("pykokoro", kwargs))

        original_onxx = tts_manager.KokoroOnnxTTSEngine
        original_pykokoro = tts_manager.PyKokoroTTSEngine
        original_env = os.environ.get("AI_ADVENTURE_TTS_ENGINE")

        try:
            os.environ.pop("AI_ADVENTURE_TTS_ENGINE", None)
            tts_manager.KokoroOnnxTTSEngine = FakeOnnxEngine
            tts_manager.PyKokoroTTSEngine = FakePyKokoroEngine

            engine = tts_manager._create_preferred_tts_engine(
                model_path="model.onnx",
                voices_path="voices.bin",
                output_directory="out",
            )

            self.assertIsInstance(engine, FakeOnnxEngine)
            self.assertEqual([call[0] for call in calls], ["onnx"])
        finally:
            tts_manager.KokoroOnnxTTSEngine = original_onxx
            tts_manager.PyKokoroTTSEngine = original_pykokoro
            if original_env is None:
                os.environ.pop("AI_ADVENTURE_TTS_ENGINE", None)
            else:
                os.environ["AI_ADVENTURE_TTS_ENGINE"] = original_env

    def test_preferred_tts_engine_allows_explicit_pykokoro(self) -> None:
        calls = []

        class FakeOnnxEngine:
            def __init__(self, **kwargs):
                calls.append(("onnx", kwargs))

        class FakePyKokoroEngine:
            def __init__(self, **kwargs):
                calls.append(("pykokoro", kwargs))

        original_onxx = tts_manager.KokoroOnnxTTSEngine
        original_pykokoro = tts_manager.PyKokoroTTSEngine
        original_env = os.environ.get("AI_ADVENTURE_TTS_ENGINE")

        try:
            os.environ["AI_ADVENTURE_TTS_ENGINE"] = "pykokoro"
            tts_manager.KokoroOnnxTTSEngine = FakeOnnxEngine
            tts_manager.PyKokoroTTSEngine = FakePyKokoroEngine

            engine = tts_manager._create_preferred_tts_engine(
                model_path="model.onnx",
                voices_path="voices.bin",
                output_directory="out",
            )

            self.assertIsInstance(engine, FakePyKokoroEngine)
            self.assertEqual([call[0] for call in calls], ["pykokoro"])
        finally:
            tts_manager.KokoroOnnxTTSEngine = original_onxx
            tts_manager.PyKokoroTTSEngine = original_pykokoro
            if original_env is None:
                os.environ.pop("AI_ADVENTURE_TTS_ENGINE", None)
            else:
                os.environ["AI_ADVENTURE_TTS_ENGINE"] = original_env

    def test_packaged_audio_paths_resolve_to_current_folder_layout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = AppPaths(
                app_data_dir=root,
                saves_dir=root / "saves",
                logs_dir=root / "logs",
                log_file=root / "logs" / "ai_adventure.log",
            )
            sound_directory = prepare_sound_directory(paths)
            sound_effect_directory = prepare_sound_effect_directory(paths)

            self.assertEqual(sound_directory, paths.package_music_tracks_dir)
            self.assertTrue((sound_directory / "Boss_Fight.mp3").exists())
            self.assertEqual(
                sound_effect_directory,
                paths.package_sound_effects_dir,
            )
            self.assertEqual(paths.package_sound_effects_dir.name, "sound_effects")
            self.assertTrue(
                any(path.suffix.casefold() == ".mp3" for path in sound_effect_directory.iterdir())
            )
            self.assertEqual(paths.app_icon_path, paths.package_data_dir / "app_icon.ico")
            self.assertTrue(paths.app_icon_path.exists())
            self.assertEqual(paths.kokoro_model_path, paths.package_tts_dir / "kokoro-v1.0.onnx")
            self.assertEqual(paths.kokoro_voices_path, paths.package_tts_dir / "voices-v1.0.bin")
            self.assertTrue(paths.kokoro_model_path.exists())
            self.assertTrue(paths.kokoro_voices_path.exists())


if __name__ == "__main__":
    unittest.main()
