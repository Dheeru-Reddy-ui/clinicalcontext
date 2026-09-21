"""Autocomplete schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SuggestionKind = Literal["mesh", "history"]


class Suggestion(BaseModel):
    value: str
    kind: SuggestionKind
    # MeSH: how many corpus documents carry the term. History: times asked.
    weight: int


class SuggestOut(BaseModel):
    query: str
    suggestions: list[Suggestion]
    took_ms: float
