from decimal import Decimal

import pytest

from wallet.accounts import Account, InvalidAmount
from wallet.transfers import InsufficientFunds, transfer


def test_transfer_moves_the_amount_and_charges_the_fee_to_the_sender() -> None:
    source = Account("alice", Decimal("200.00"))
    target = Account("bob", Decimal("10.00"))

    transfer(source, target, Decimal("100.00"))

    assert source.balance == Decimal("98.50")
    assert target.balance == Decimal("110.00")


def test_transfer_needs_enough_money_for_the_fee_too() -> None:
    source = Account("alice", Decimal("100.00"))
    target = Account("bob")

    with pytest.raises(InsufficientFunds):
        transfer(source, target, Decimal("100.00"))

    assert source.balance == Decimal("100.00")
    assert target.balance == Decimal("0")


def test_transfer_rejects_non_positive_amounts() -> None:
    source = Account("alice", Decimal("100.00"))

    with pytest.raises(InvalidAmount):
        transfer(source, Account("bob"), Decimal("0"))
