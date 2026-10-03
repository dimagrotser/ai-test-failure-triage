import re
from collections.abc import Callable

Replacement = str | Callable[[re.Match[str]], str]

# A truncated block (log cut before the END line) is removed up to the end of the text.
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)", re.DOTALL
)
JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "<CARD>" if _luhn_valid(digits) else match.group()


# Order matters: a private key or JWT must go before the looser patterns see their pieces.
RULES: list[tuple[re.Pattern[str], Replacement]] = [
    (PRIVATE_KEY, "<PRIVATE_KEY>"),
    (JWT, "<JWT>"),
    (EMAIL, "<EMAIL>"),
    (CARD, _card),
]
