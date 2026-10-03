from decimal import Decimal


class InvalidAmount(ValueError):
    pass


class Account:
    def __init__(self, owner: str, balance: Decimal = Decimal("0")) -> None:
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: Decimal) -> None:
        if amount <= 0:
            raise InvalidAmount(f"amount must be positive, got {amount}")
        self.balance += amount
