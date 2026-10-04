import re

from failtriage.classify.provider import Usage

# USD per million tokens, (input, output). Update when Anthropic changes its prices.
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cost_usd(model: str, usage: Usage) -> float | None:
    """Price of a run, or None when the model is not in the table."""
    # A dated snapshot such as claude-haiku-4-5-20251001 costs what its alias costs.
    price = PRICES.get(re.sub(r"-\d{8}$", "", model))
    if price is None:
        return None
    return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1_000_000
