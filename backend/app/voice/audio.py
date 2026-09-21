"""PCM utilities shared by the providers, the session, and the eval harness.

Everything is 16 kHz, 16-bit, mono, little-endian. The energy VAD here is
deliberately simple and fully deterministic: an adaptive noise floor plus a
speech threshold above it. In the cloud backend Deepgram's VAD events are the
primary signal; offline, this is the acoustic layer of the endpoint decision.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.voice.protocol import FRAME_BYTES, FRAME_MS, SAMPLE_RATE


def pcm16_to_float(pcm: bytes) -> NDArray[np.float32]:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def float_to_pcm16(samples: NDArray[np.float32] | NDArray[np.float64]) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return bytes((clipped * 32767.0).astype(np.int16).tobytes())


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    samples = pcm16_to_float(pcm)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


def frames(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    """Split PCM into fixed frames; the last partial frame is zero-padded."""
    out = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    if out and len(out[-1]) < frame_bytes:
        out[-1] = out[-1] + b"\x00" * (frame_bytes - len(out[-1]))
    return out


def duration_ms(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / 2 / sample_rate * 1000.0


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE) -> bytes:
    """Linear-interpolation resample. Adequate for speech at these rates."""
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = pcm16_to_float(pcm)
    n_out = round(samples.size * dst_rate / src_rate)
    if n_out <= 1:
        return b""
    x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return float_to_pcm16(np.interp(x_new, x_old, samples).astype(np.float32))


def read_wav(data: bytes) -> tuple[bytes, int]:
    """→ (mono pcm16, sample_rate). Stereo is down-mixed; 8/24-bit rejected."""
    with wave.open(io.BytesIO(data)) as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"unsupported sample width {width}")
    if channels == 2:
        stereo = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.int32)
        raw = ((stereo[:, 0] + stereo[:, 1]) // 2).astype(np.int16).tobytes()
    elif channels != 1:
        raise ValueError(f"unsupported channel count {channels}")
    return raw, rate


def write_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def apply_gain(pcm: bytes, gain_db: float) -> bytes:
    return float_to_pcm16(pcm16_to_float(pcm) * float(10 ** (gain_db / 20)))


def silence(ms: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(sample_rate * ms / 1000)


def mix_noise(pcm: bytes, snr_db: float, *, seed: int = 0) -> bytes:
    """Add pink-ish noise at a given SNR (for the noise fixture variants).

    The noise is 1/f-shaped white noise (a first-order low-pass), scaled so
    the resulting signal-to-noise ratio is exactly ``snr_db`` over the clip.
    """
    signal = pcm16_to_float(pcm)
    if signal.size == 0:
        return pcm
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(signal.size).astype(np.float32)
    pink = np.empty_like(white)
    acc = 0.0
    for i, w in enumerate(white):  # cheap IIR: y = 0.97 y + 0.03 x
        acc = 0.97 * acc + 0.03 * float(w)
        pink[i] = acc
    signal_power = float(np.mean(signal * signal)) or 1e-9
    noise_power = float(np.mean(pink * pink)) or 1e-9
    target_noise_power = signal_power / (10 ** (snr_db / 10))
    pink *= np.sqrt(target_noise_power / noise_power)
    return float_to_pcm16(signal + pink)


@dataclass(slots=True)
class VadDecision:
    speech: bool
    level: float
    floor: float


class EnergyVad:
    """Adaptive-floor energy VAD over 20 ms frames.

    ``floor`` tracks the quietest recent frames (fast down, slow up), and a
    frame is speech when its RMS exceeds ``max(floor * ratio, absolute_min)``.
    Speech onset requires ``onset_frames`` consecutive speech frames so a click
    does not open a turn; offset is the caller's business (endpointing).
    """

    def __init__(
        self,
        *,
        ratio: float = 3.5,
        absolute_min: float = 0.008,
        onset_frames: int = 2,
    ) -> None:
        self._ratio = ratio
        self._absolute_min = absolute_min
        self._onset_frames = onset_frames
        self._floor = 0.004
        self._run = 0
        self.in_speech = False

    @property
    def floor(self) -> float:
        return self._floor

    def process(self, frame: bytes) -> VadDecision:
        level = rms(frame)
        threshold = max(self._floor * self._ratio, self._absolute_min)
        loud = level > threshold
        if loud:
            # Speech frames barely move the floor: a long loud utterance must
            # not raise it until the speech itself falls under the threshold.
            self._floor = min(self._floor * 1.002 + 1e-6, level / self._ratio)
        elif level < self._floor:
            self._floor = 0.8 * self._floor + 0.2 * level
        else:
            self._floor = 0.95 * self._floor + 0.05 * level
        if loud:
            self._run += 1
            if self._run >= self._onset_frames:
                self.in_speech = True
        else:
            self._run = 0
            self.in_speech = False
        return VadDecision(speech=self.in_speech, level=level, floor=self._floor)

    def reset(self) -> None:
        self._run = 0
        self.in_speech = False


def trim_silence(pcm: bytes, *, threshold: float = 0.01, pad_frames: int = 8) -> bytes:
    """Cut leading/trailing silence (keeps ``pad_frames`` of context).

    Whisper hallucinates on long trailing silence, so the offline STT trims
    before decoding; the same helper keeps the fixture corpus tight.
    """
    parts = frames(pcm)
    if not parts:
        return pcm
    loud = [i for i, f in enumerate(parts) if rms(f) > threshold]
    if not loud:
        return pcm
    start = max(0, loud[0] - pad_frames)
    end = min(len(parts), loud[-1] + pad_frames + 1)
    return b"".join(parts[start:end])


__all__ = [
    "FRAME_BYTES",
    "FRAME_MS",
    "SAMPLE_RATE",
    "EnergyVad",
    "VadDecision",
    "apply_gain",
    "duration_ms",
    "float_to_pcm16",
    "frames",
    "mix_noise",
    "pcm16_to_float",
    "read_wav",
    "resample_pcm16",
    "rms",
    "silence",
    "trim_silence",
    "write_wav",
]
