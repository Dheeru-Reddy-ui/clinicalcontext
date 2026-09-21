"""Streaming sentence segmentation for the TTS pipeline (11F.2).

Tokens arrive as deltas; a sentence is released at a boundary once it is at
least ``min_chars`` long (so TTS never receives a choppy three-word fragment
that would break prosody), and whatever remains is flushed at the end.
Citation markers stay attached to their sentence — "… [2]. Next" splits after
the marker, not before it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A boundary is sentence-final punctuation, any citation markers that
# follow it (". [1] [2]"), then whitespace before the next sentence — which
# must not itself be a marker, or "claim. [6]" would strand the citation.
_BOUNDARY = re.compile(
    r"(?<=[.!?])(?:\s*(?:\[\d+(?:,\s*\d+)*\]))*\s+(?=[A-Z0-9\"'(])"
    r"|(?<=[.!?])(?:\s*\[\d+(?:,\s*\d+)*\])+\s*$"
)
_ABBREVIATIONS = ("e.g.", "i.e.", "vs.", "et al.", "approx.", "dr.", "mr.", "mrs.", "fig.", "no.")


@dataclass(slots=True)
class SentenceSegmenter:
    min_chars: int = 40
    _buffer: str = field(default="", init=False)

    def push(self, delta: str) -> list[str]:
        self._buffer += delta
        return self._drain()

    def flush(self) -> list[str]:
        out = self._drain()
        rest = self._buffer.strip()
        self._buffer = ""
        if rest:
            out.append(rest)
        return out

    def _drain(self) -> list[str]:
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                return out
            candidate = self._buffer[:cut].strip()
            if len(candidate) < self.min_chars:
                # Too short to ship alone: extend to the next boundary, or
                # wait for one (on flush the caller takes the rest whole).
                next_cut = self._find_cut(start=cut)
                if next_cut is None:
                    return out
                cut = next_cut
                candidate = self._buffer[:cut].strip()
            out.append(candidate)
            self._buffer = self._buffer[cut:].lstrip()

    def _find_cut(self, *, start: int = 0) -> int | None:
        for match in _BOUNDARY.finditer(self._buffer, start):
            end = match.end()
            head = self._buffer[:end].rstrip()
            # Do not split on a trailing abbreviation ("e.g. metformin").
            if any(head.lower().endswith(abbr) for abbr in _ABBREVIATIONS):
                continue
            # Only split when the marker (if any) is fully closed.
            if head.count("[") != head.count("]"):
                continue
            return end
        return None


def split_sentences(text: str, min_chars: int = 0) -> list[str]:
    segmenter = SentenceSegmenter(min_chars=min_chars)
    out = segmenter.push(text)
    out.extend(segmenter.flush())
    return [s for s in out if s]
