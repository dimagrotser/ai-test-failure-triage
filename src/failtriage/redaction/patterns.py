import re
from collections.abc import Callable

Replacement = str | Callable[[re.Match[str]], str]

# A truncated block (log cut before the END line) is removed up to the end of the text.
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)", re.DOTALL
)
JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
# Header values are removed whole: a Cookie holds several secrets and Basic auth has no prefix.
AUTH_HEADER = re.compile(
    r"""((?:proxy-)?authorization|set-cookie|cookie)(['"]?\s*[:=]\s*)(?:(['"]).*?\3|[^\r\n]+)""",
    re.IGNORECASE,
)
TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,})"
)
URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]*:)[^\s/]+(@)", re.IGNORECASE)
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
    (AUTH_HEADER, r"\1\2<SECRET>"),
    (TOKEN, "<TOKEN>"),
    (URL_PASSWORD, r"\1<SECRET>\2"),
    (EMAIL, "<EMAIL>"),
    (CARD, _card),
]
