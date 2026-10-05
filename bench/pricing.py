"""USD per million tokens (Claude API list prices). Cache writes cost 1.25x input; reads 0.1x."""

PRICES = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def cost(model: str, m: dict) -> float:
    inp, out = PRICES[model]
    return (m.get("input_tokens", 0) * inp
            + m.get("cache_write_tokens", 0) * inp * 1.25
            + m.get("cache_read_tokens", 0) * inp * 0.10
            + m.get("output_tokens", 0) * out) / 1_000_000
