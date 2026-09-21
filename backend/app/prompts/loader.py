"""Versioned prompt loading.

Prompts live as ``{name}.v{N}.md`` files next to this module and are loaded
at runtime — never inlined in code. The returned ``PromptText`` carries the
version tag that gets recorded in traces and classification records.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class PromptText:
    name: str
    version: int
    text: str

    @property
    def version_tag(self) -> str:
        return f"{self.name}.v{self.version}"


@lru_cache
def load_prompt(name: str, version: int) -> PromptText:
    path = _PROMPTS_DIR / f"{name}.v{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path.name}")
    return PromptText(name=name, version=version, text=path.read_text(encoding="utf-8"))
