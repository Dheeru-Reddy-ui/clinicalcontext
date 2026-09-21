"""Build the microphone files the Playwright voice suite plays (Phase 14.2).

Chromium's `--use-file-for-fake-audio-capture` *loops* the file it is given.
The voice harness fixtures are the bare utterance, so on loop the recognizer
never hears the speaker stop — a turn would never commit — and once one does,
the next loop barges in on the agent mid-answer. Appending a long silence
gives the endpointer its pause and the agent room to reply.

    uv run python -m scripts.make_e2e_audio
"""

from __future__ import annotations

import wave
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "evals" / "voice" / "fixtures" / "audio"
TARGET = Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / "audio"
# Long enough for a cold offline turn (retrieval + extractive answer + TTS)
# to finish before the question comes round again.
SILENCE_SECONDS = 60.0
FIXTURES = ("golden-01", "lasa-01", "adv-01")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FIXTURES:
        with wave.open(str(SOURCE / f"{name}.wav")) as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
        silence = b"\x00" * (
            int(params.framerate * SILENCE_SECONDS) * params.sampwidth * params.nchannels
        )
        out = TARGET / f"{name}-padded.wav"
        with wave.open(str(out), "wb") as target:
            target.setparams(params)
            target.writeframes(frames + silence)
        seconds = len(frames + silence) / params.sampwidth / params.nchannels / params.framerate
        print(f"{out.name}: {seconds:.1f}s")


if __name__ == "__main__":
    main()
