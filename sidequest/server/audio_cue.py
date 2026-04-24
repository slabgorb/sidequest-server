"""Build AUDIO_CUE wire payload from AudioInterpreter output."""

from __future__ import annotations

from sidequest.audio.library_backend import LibraryBackend
from sidequest.audio.models import AudioCue, AudioLane
from sidequest.protocol.messages import AudioCuePayload


def build_audio_cue_payload(
    cues: list[AudioCue],
    *,
    audio_backend: LibraryBackend | None = None,
    genre_slug: str | None = None,
) -> AudioCuePayload:
    """Convert AudioInterpreter output to an AudioCuePayload wire object.

    Args:
        cues: AudioCue list from AudioInterpreter.interpret().
        audio_backend: Optional LibraryBackend used to resolve each cue to a
            file path. When provided, music_track and each sfx_triggers
            entry are library-relative paths (relative to
            ``audio_backend.base_path``). When absent, music_track stays
            ``None`` and sfx_triggers carry the raw sfx_id.
        genre_slug: Optional genre pack slug. When provided, each resolved
            pack-relative path is prefixed with ``/genre/{genre_slug}/``
            so the client can fetch directly via the FastAPI static mount
            for the genre pack dir (app.py line 207). Without the prefix,
            a path like ``audio/music/foo.ogg`` is fetched relative to
            the Vite dev server root (``http://localhost:5173/audio/...``)
            which 404s and surfaces as "Unable to decode audio data" on
            every narration turn (playtest 2026-04-24). Leave ``None`` for
            tests / CLI consumers that don't live behind the HTTP mount.

    Returns:
        Fully-populated AudioCuePayload. All fields default to
        empty/None when no matching cues exist.
    """
    mood: str | None = None
    music_track: str | None = None
    sfx_triggers: list[str] = []

    def _maybe_prefix(relative: str) -> str:
        """Wrap a pack-relative path with the /genre/{slug}/ mount prefix
        so the client's ``AudioCache`` fetches it from the server's
        static file mount rather than the Vite dev-server root. Paths
        that are already absolute URLs (``http://…``, ``https://…``) or
        server-absolute (``/genre/…``) pass through untouched so callers
        that pre-wrap don't double-prefix.
        """
        if not relative:
            return relative
        if relative.startswith(("http://", "https://", "/")):
            return relative
        if genre_slug is None:
            return relative
        return f"/genre/{genre_slug}/{relative}"

    for cue in cues:
        if cue.lane == AudioLane.MUSIC and cue.mood is not None:
            mood = cue.mood
            if audio_backend is not None:
                resolved = _relative_to_backend(audio_backend, cue)
                music_track = (
                    _maybe_prefix(resolved) if resolved is not None else None
                )
        elif cue.lane == AudioLane.SFX and cue.sfx_id is not None:
            entry = cue.sfx_id
            if audio_backend is not None:
                rel = _relative_to_backend(audio_backend, cue)
                if rel is not None:
                    entry = _maybe_prefix(rel)
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
