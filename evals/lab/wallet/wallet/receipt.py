from decimal import Decimal

from wallet.accounts import Account
from wallet.fees import transfer_fee


def render_receipt(source: Account, target: Account, amount: Decimal) -> str:
    return (
        f"<h1>Transfer to {target.owner}</h1>\n"
        f'<p>Amount: <span data-testid="transfer-amount">{amount}</span></p>\n'
        f'<p>Fee: <span data-testid="transfer-fee">{transfer_fee(amount)}</span></p>\n'
        f'<p>Balance: <span data-testid="sender-balance">{source.balance}</span></p>\n'
    )
