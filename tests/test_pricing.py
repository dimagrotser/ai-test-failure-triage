import pytest

from failtriage.classify.pricing import cost_usd
from failtriage.classify.provider import Usage


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-5-5", 2 + 10),
        ("claude-opus-5-5", 4 + 20),
        ("claude-haiku-4-5", 1 + 5),
    ],
)
def test_cost_is_priced_per_million_tokens(model: str, expected: float) -> None:
    usage = Usage(calls=1, input_tokens=1_000_000, output_tokens=1_000_000)

    assert cost_usd(model, usage) == pytest.approx(expected)


def test_cost_counts_input_and_output_separately() -> None:
    usage = Usage(calls=2, input_tokens=4000, output_tokens=500)

    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(0.008 + 0.005)


def test_a_dated_haiku_id_uses_the_haiku_price() -> None:
    usage = Usage(calls=1, input_tokens=1_000_000, output_tokens=0)

    assert cost_usd("claude-haiku-4-5-20251001", usage) == pytest.approx(1)


def test_an_unknown_model_has_no_cost() -> None:
    assert cost_usd("claude-mystery", Usage(calls=1, input_tokens=10, output_tokens=10)) is None
