from decimal import Decimal

from wallet.accounts import Account, InvalidAmount
from wallet.fees import transfer_fee

TRANSFER_LIMIT = Decimal("1000.00")


class LimitExceeded(Exception):
    pass


class InsufficientFunds(Exception):
    pass


def transfer(source: Account, target: Account, amount: Decimal) -> None:
    if amount <= 0:
        raise InvalidAmount(f"amount must be positive, got {amount}")
    if amount > TRANSFER_LIMIT:
        raise LimitExceeded(f"{amount} is above the limit of {TRANSFER_LIMIT}")
    total = amount + transfer_fee(amount)
    if source.balance < total:
        raise InsufficientFunds(f"{source.owner} has {source.balance}, needs {total}")
    source.balance -= total
    target.balance += amount
