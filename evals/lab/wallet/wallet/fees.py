from decimal import ROUND_HALF_UP, Decimal

FEE_RATE = Decimal("0.015")
CENT = Decimal("0.01")


def transfer_fee(amount: Decimal) -> Decimal:
    return (amount * FEE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
