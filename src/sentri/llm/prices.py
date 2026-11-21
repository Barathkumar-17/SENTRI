"""Per-million-token prices (USD) for cost tracking. First-party API rates."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_warned: set[str] = set()

PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        # Served model ids can carry suffixes; match on the longest known prefix.
        matches = [k for k in PRICES if model.startswith(k)]
        price = PRICES[max(matches, key=len)] if matches else None
    if price is None:
        if model not in _warned:
            _warned.add(model)
            log.info("no price known for model %s (local or third-party); cost recorded as 0", model)
        return 0.0
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
