from decimal import Decimal

import pytest

from wallet.accounts import Account, InvalidAmount


def test_new_account_has_the_opening_balance() -> None:
    assert Account("alice", Decimal("50.00")).balance == Decimal("50.00")


def test_deposit_increases_the_balance() -> None:
    account = Account("alice", Decimal("50.00"))

    account.deposit(Decimal("25.50"))

    assert account.balance == Decimal("75.50")


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-5.00")])
def test_deposit_rejects_non_positive_amounts(amount: Decimal) -> None:
    account = Account("alice", Decimal("50.00"))

    with pytest.raises(InvalidAmount):
        account.deposit(amount)

    assert account.balance == Decimal("50.00")
