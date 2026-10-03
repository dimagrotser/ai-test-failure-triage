from decimal import Decimal

import pytest

from wallet.accounts import Account
from wallet.transfers import LimitExceeded, transfer


def test_transfer_up_to_the_limit_is_allowed() -> None:
    source = Account("alice", Decimal("5000.00"))
    target = Account("bob")

    transfer(source, target, Decimal("1000.00"))

    assert target.balance == Decimal("1000.00")


def test_transfer_above_the_limit_is_rejected() -> None:
    source = Account("alice", Decimal("5000.00"))
    target = Account("bob")

    with pytest.raises(LimitExceeded):
        transfer(source, target, Decimal("1000.01"))

    assert source.balance == Decimal("5000.00")
    assert target.balance == Decimal("0")
