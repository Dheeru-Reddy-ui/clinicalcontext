"""The voices a person can choose for answers read aloud.

A short list of Deepgram Aura-2 English voices, picked for reading health
information: calm and clear rather than chirpy, with American, British and
Australian accents and both female and male voices. The tone words are
Deepgram's own descriptions, shortened. Settings store only the short name
(``thalia``), so a voice can be added here without a migration; a name that
is no longer offered falls back to the default rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_VOICE = "thalia"


@dataclass(frozen=True, slots=True)
class Voice:
    id: str
    label: str
    gender: Literal["female", "male"]
    accent: Literal["American", "British", "Australian"]
    tone: str

    @property
    def deepgram_model(self) -> str:
        return f"aura-2-{self.id}-en"


VOICES: tuple[Voice, ...] = (
    Voice("thalia", "Thalia", "female", "American", "Clear and confident"),
    Voice("athena", "Athena", "female", "American", "Calm and professional"),
    Voice("harmonia", "Harmonia", "female", "American", "Empathetic and calm"),
    Voice("orion", "Orion", "male", "American", "Approachable and calm"),
    Voice("neptune", "Neptune", "male", "American", "Professional and patient"),
    Voice("pandora", "Pandora", "female", "British", "Smooth and calm"),
    Voice("draco", "Draco", "male", "British", "Warm and trustworthy"),
    Voice("theia", "Theia", "female", "Australian", "Expressive and sincere"),
    Voice("hyperion", "Hyperion", "male", "Australian", "Caring and warm"),
)

_BY_ID = {voice.id: voice for voice in VOICES}


def find_voice(voice_id: str | None) -> Voice | None:
    return _BY_ID.get(voice_id or "")


def deepgram_model_for(voice_id: str | None) -> str | None:
    """The Aura-2 model for a voice name, or None to use the server's default."""
    voice = find_voice(voice_id)
    return voice.deepgram_model if voice else None
