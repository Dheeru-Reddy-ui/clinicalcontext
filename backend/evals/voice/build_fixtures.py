"""Render the voice fixture corpus (11H.1).

    uv run python -m evals.voice.build_fixtures [--out evals/voice/fixtures]

Every fixture in ``manifest.py`` is synthesized with the named OneCore voice
(Windows WinRT; en-GB George/Hazel/Susan and en-IN Heera/Ravi), silence is
trimmed to a short pad, hesitation fixtures use SSML breaks so the pauses
are real pauses in the audio, and noise variants mix pink noise at the
stated SNR. Output: 16 kHz mono PCM WAVs plus ``manifest.jsonl`` recording
exactly how each file was produced.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.voice.audio import apply_gain, mix_noise, silence, trim_silence, write_wav
from evals.voice.manifest import Fixture, build_manifest

_HERE = Path(__file__).resolve().parent
DEFAULT_OUT = _HERE / "fixtures"

_SSML = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
    "{body}</speak>"
)


async def _synthesize(fixture: Fixture) -> bytes:
    # faster-whisper must be imported before WinRT in this process (see
    # app.voice.tts.local); the local module handles the order.
    from app.voice.tts.local import synthesize_winrt

    if fixture.ssml is not None:
        if sys.platform != "win32":
            raise RuntimeError("SSML fixtures need the WinRT synthesizer (Windows)")
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader

        from app.voice.audio import read_wav, resample_pcm16

        synthesizer = SpeechSynthesizer()
        chosen = next(
            (
                v
                for v in SpeechSynthesizer.all_voices
                if fixture.voice.lower() in v.display_name.lower()
            ),
            None,
        )
        if chosen is None:
            raise RuntimeError(f"voice not installed: {fixture.voice}")
        synthesizer.voice = chosen
        ssml = _SSML.format(lang=fixture.language, body=fixture.ssml)
        stream = await synthesizer.synthesize_ssml_to_stream_async(ssml)
        reader = DataReader(stream.get_input_stream_at(0))
        size = int(stream.size)
        await reader.load_async(size)
        buffer = bytearray(size)
        reader.read_bytes(buffer)
        pcm, rate = read_wav(bytes(buffer))
        return resample_pcm16(pcm, rate)
    return await synthesize_winrt(fixture.text, fixture.voice)


async def build(out: Path) -> list[Fixture]:
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    fixtures = build_manifest()
    for fixture in fixtures:
        pcm = await _synthesize(fixture)
        # Keep ~160 ms of leading/trailing room; the harness adds its own
        # silence to trigger endpointing.
        pcm = trim_silence(pcm, pad_frames=8)
        if fixture.noise_snr_db is not None:
            pcm = mix_noise(pcm, fixture.noise_snr_db, seed=hash(fixture.id) & 0xFFFF)
        if fixture.gain_db:
            pcm = apply_gain(pcm, fixture.gain_db)
        pcm = silence(100) + pcm + silence(100)
        (out / fixture.file).write_bytes(write_wav(pcm))
        print(f"{fixture.id:16s} {fixture.voice:18s} {len(pcm) / 32000:5.1f}s  {fixture.text[:60]}")
    with (out / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for fixture in fixtures:
            fh.write(json.dumps(fixture.as_dict(), ensure_ascii=False) + "\n")
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    fixtures = asyncio.run(build(args.out))
    print(f"\nwrote {len(fixtures)} fixtures to {args.out}")


if __name__ == "__main__":
    main()
