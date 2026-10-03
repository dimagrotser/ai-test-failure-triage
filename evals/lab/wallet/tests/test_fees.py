from decimal import Decimal

import pytest

from wallet import fees
from wallet.fees import transfer_fee


@pytest.mark.parametrize(
    ("amount", "fee"),
    [
        ("100.00", "1.50"),
        ("99.00", "1.49"),
        ("10.00", "0.15"),
        ("0.01", "0.00"),
    ],
)
def test_fee_is_one_and_a_half_percent_rounded_half_up(amount: str, fee: str) -> None:
    assert transfer_fee(Decimal(amount)) == Decimal(fee)


def test_zero_fee_rate_makes_transfers_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fees, "FEE_RATE", Decimal("0"))

    assert transfer_fee(Decimal("100.00")) == Decimal("0.00")
