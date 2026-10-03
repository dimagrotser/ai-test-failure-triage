from decimal import Decimal

from wallet.accounts import Account
from wallet.ledger import record_transfer


def test_transfer_is_recorded_in_the_ledger() -> None:
    entry_id = record_transfer(Account("alice"), Account("bob"), Decimal("100.00"))

    assert entry_id == "ledger-1"
