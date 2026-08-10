from __future__ import annotations

from collections.abc import Iterable


def distinct_audio_track_catalogs(
    music_tracks: Iterable[object] | None,
    sound_effect_tracks: Iterable[object] | None,
) -> tuple[list[str], list[str]]:
    """Returns deduplicated, disjoint music and one-shot effect filenames."""

    clean_music = _unique_track_names(music_tracks)
    music_keys = {track.casefold() for track in clean_music}
    clean_effects = [
        track
        for track in _unique_track_names(sound_effect_tracks)
        if track.casefold() not in music_keys
    ]
    return clean_music, clean_effects


def _unique_track_names(tracks: Iterable[object] | None) -> list[str]:
    """Normalizes track names while preserving the first spelling and order."""

    result: list[str] = []
    seen: set[str] = set()
    for raw_track in tracks or ():
        track = str(raw_track or "").strip()
        key = track.casefold()
        if track and key not in seen:
            result.append(track)
            seen.add(key)
    return result
