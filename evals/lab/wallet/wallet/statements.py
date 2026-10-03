import os
from pathlib import Path

from wallet.accounts import Account


def export_statement(account: Account) -> Path:
    path = Path(os.environ["WALLET_STATEMENTS_DIR"]) / f"{account.owner}.csv"
    path.write_text(f"owner,balance\n{account.owner},{account.balance}\n")
    return path
