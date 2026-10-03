import re
from collections.abc import Callable

Replacement = str | Callable[[re.Match[str]], str]

# A truncated block (log cut before the END line) is removed up to the end of the text.
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)", re.DOTALL
)
JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")


def _mask_value(match: re.Match[str]) -> str:
    quote = match.group("quote") or ""
    return f"{match.group('head')}{quote}<SECRET>{quote}"


def _mask_header(match: re.Match[str]) -> str:
    quote = match.group("quote") or ""
    return f"{match.group('head')}{match.group('sep')}{quote}<SECRET>{quote}"


# Quoted values keep their quotes so JSON and shell commands stay readable. An unquoted value
# runs to the end of the line, or to the next quote when it sits inside a quoted command.
AUTH_HEADER = re.compile(
    r"""(?P<head>(?:proxy-)?authorization|set-cookie|cookie)(?P<sep>['"]?\s*[:=]\s*)"""
    r"""(?:(?P<quote>['"])(?:\\.|(?!(?P=quote)).)*(?P=quote)|[^\r\n'"]+)""",
    re.IGNORECASE,
)
BEARER = re.compile(r"\b(Bearer\s+)[\w.~+/=-]{8,}", re.IGNORECASE)
TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}"
    r"|npm_[A-Za-z0-9]{30,}|glpat-[\w-]{20,}|AIza[\w-]{35})"
)
URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]*:)[^\s/]+(@)", re.IGNORECASE)

# Env dumps, query strings and dict reprs: the value goes, the key stays so the text still reads.
# Values that are already a typed placeholder are kept, which also makes the rules idempotent.
_VALUE = r"""(?!<[A-Z_]+>)(?:(?P<quote>['"])(?:\\.|(?!(?P=quote)).)*(?P=quote)|[^\s,;&}\])]+)"""
# *_KEY, *_TOKEN and friends may have spaces around the operator, as in `db_password = x`.
SUFFIXED_SECRET = re.compile(
    r"""(?<!\w)(?!(?:primary|foreign)_key\b)(?P<head>[\w.-]*_(?:KEY|TOKEN|SECRET|PASSWORD)"""
    r"""['"]?\s*[:=]\s*)""" + _VALUE,
    re.IGNORECASE,
)
# Bare words and camelCase names are also common in code, so only the tight `name=value` and
# `name: value` forms count. `token = fetch()` and `assert token == 'x'` are left alone.
LOOSE_SECRET = re.compile(
    r"""(?<!\w)(?P<head>(?:password|passwd|secret|token|api[_-]?key|\w*(?-i:[a-z](?:Key|Token|Secret|Password)))"""
    r"""['"]?[:=]\s*)(?![=~])""" + _VALUE,
    re.IGNORECASE,
)
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
    (AUTH_HEADER, _mask_header),
    (BEARER, r"\1<SECRET>"),
    (TOKEN, "<TOKEN>"),
    (URL_PASSWORD, r"\1<SECRET>\2"),
    (SUFFIXED_SECRET, _mask_value),
    (LOOSE_SECRET, _mask_value),
    (EMAIL, "<EMAIL>"),
    (CARD, _card),
]
