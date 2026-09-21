"""Token usage and USD cost accounting.

Cost is computed from *actual* provider-reported token counts, never
estimated: each LLM/embedding call appends its real usage to a per-run
:class:`UsageAccumulator`, and :func:`compute_cost_usd` prices it against the
published per-million-token rates. The offline heuristic backend reports no
usage, so its cost is a truthful $0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1M tokens. Update when provider pricing changes (single source here).
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input $/1M, output $/1M)
    "claude-sonnet-4-6": (3.00, 15.00),
    "heuristic": (0.0, 0.0),
}
# Cohere embed/rerank priced per 1M tokens / per 1k searches; embeddings are a
# rounding error next to generation, tracked separately and coarsely.
_EMBED_PER_1M = 0.12  # embed-v4.0
_RERANK_PER_1K_SEARCHES = 2.0  # rerank-v3.5


@dataclass(slots=True)
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class UsageAccumulator:
    """Collects every model call's usage across one query run."""

    calls: list[Usage] = field(default_factory=list)

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls.append(Usage(model, input_tokens, output_tokens))

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.calls)

    @property
    def cost_usd(self) -> float:
        total = 0.0
        for usage in self.calls:
            in_rate, out_rate = _PRICING.get(usage.model, (0.0, 0.0))
            total += usage.input_tokens / 1_000_000 * in_rate
            total += usage.output_tokens / 1_000_000 * out_rate
        return round(total, 6)


def embedding_cost_usd(token_count: int) -> float:
    return round(token_count / 1_000_000 * _EMBED_PER_1M, 6)
