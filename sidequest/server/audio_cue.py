"""Build AUDIO_CUE wire payload from AudioInterpreter output."""

from __future__ import annotations

from sidequest.audio.library_backend import LibraryBackend
from sidequest.audio.models import AudioCue, AudioLane
from sidequest.protocol.messages import AudioCuePayload


def build_audio_cue_payload(
    cues: list[AudioCue],
    *,
    audio_backend: LibraryBackend | None = None,
) -> AudioCuePayload:
    """Convert AudioInterpreter output to an AudioCuePayload wire object.

    Args:
        cues: AudioCue list from AudioInterpreter.interpret().
        audio_backend: Optional LibraryBackend used to resolve each cue to a
            file path. When provided, music_track and each sfx_triggers
            entry are library-relative paths (relative to
            ``audio_backend.base_path``). When absent, music_track stays
            ``None`` and sfx_triggers carry the raw sfx_id.

    Returns:
        Fully-populated AudioCuePayload. All fields default to
        empty/None when no matching cues exist.
    """
    mood: str | None = None
    music_track: str | None = None
    sfx_triggers: list[str] = []

    for cue in cues:
        if cue.lane == AudioLane.MUSIC and cue.mood is not None:
            mood = cue.mood
            if audio_backend is not None:
                music_track = _relative_to_backend(audio_backend, cue)
        elif cue.lane == AudioLane.SFX and cue.sfx_id is not None:
            entry = cue.sfx_id
            if audio_backend is not None:
                rel = _relative_to_backend(audio_backend, cue)
                if rel is not None:
                    entry = rel
            sfx_triggers.append(entry)

    return AudioCuePayload(
        mood=mood,
        music_track=music_track,
        sfx_triggers=sfx_triggers,
    )


def _relative_to_backend(backend: LibraryBackend, cue: AudioCue) -> str | None:
    """Resolve a cue through the backend and return a base-relative string,
    or ``None`` when the backend can't resolve it."""
    resolved = backend.resolve(cue)
    if resolved is None:
        return None
    base = backend.base_path
    try:
        return str(resolved.relative_to(base))
    except ValueError:
        return str(resolved)
