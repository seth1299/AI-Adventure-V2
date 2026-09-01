from __future__ import annotations

import unittest

from ai_adventure.ui.story_bubbles import split_story_bubble_segments


class StoryBubbleTests(unittest.TestCase):
    def test_multiple_speakers_split_one_turn_in_source_order(self) -> None:
        mira_cue = {
            "anchor_text": '"Stay close."',
            "speaker_id": "mira",
            "speaker_name": "Mira",
            "voice_profile": "feminine",
            "voice_id": "af_sarah",
        }
        stranger_cue = {
            "anchor_text": '"Not that door."',
            "speaker_id": "hooded_figure",
            "speaker_name": "Hooded Figure",
            "voice_profile": "neutral",
            "voice_id": "am_echo",
        }
        sound_cue = {
            "filename": "Knock.wav",
            "anchor_text": '"Not that door."',
            "position": "before",
        }

        segments = split_story_bubble_segments(
            (
                'Rain taps the glass. "Stay close." The hooded figure points east. '
                '"Not that door." Silence returns.'
            ),
            sound_effect_cues=[sound_cue],
            speaker_cues=[stranger_cue, mira_cue],
        )

        self.assertEqual(
            [segment["speaker_name"] for segment in segments],
            ["", "Mira", "", "Hooded Figure", ""],
        )
        self.assertEqual(
            [segment["content"] for segment in segments],
            [
                "Rain taps the glass.",
                '"Stay close."',
                "The hooded figure points east.",
                '"Not that door."',
                "Silence returns.",
            ],
        )
        self.assertEqual(segments[1]["speaker_cues"], [mira_cue])
        self.assertEqual(segments[3]["speaker_cues"], [stranger_cue])
        self.assertEqual(segments[3]["sound_effect_cues"], [sound_cue])

    def test_invalid_duplicate_anchor_keeps_story_in_one_narrator_bubble(self) -> None:
        segments = split_story_bubble_segments(
            '"Wait." A bell rings. "Wait."',
            speaker_cues=[
                {
                    "anchor_text": '"Wait."',
                    "speaker_id": "guard",
                    "speaker_name": "Guard",
                    "voice_profile": "neutral",
                    "voice_id": "am_echo",
                }
            ],
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["speaker_name"], "")
        self.assertEqual(segments[0]["content"], '"Wait." A bell rings. "Wait."')


if __name__ == "__main__":
    unittest.main()
