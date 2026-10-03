from decimal import Decimal

from wallet.accounts import Account
from wallet.statements import export_statement


def test_statement_lists_the_owner_and_balance() -> None:
    path = export_statement(Account("alice", Decimal("98.50")))

    assert path.read_text() == "owner,balance\nalice,98.50\n"
