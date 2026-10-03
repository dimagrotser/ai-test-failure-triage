import pytest

from failtriage.redaction import redact

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlX3BhcnQ"
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA7vbqajDw4o6gJy8UtmIbkcpnkO3Kwc4qsEnSZp
-----END RSA PRIVATE KEY-----"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sent to jane.doe+ci@example.co.uk failed", "sent to <EMAIL> failed"),
        (f"bad token {JWT} rejected", "bad token <JWT> rejected"),
        (f"key:\n{PRIVATE_KEY}\nend", "key:\n<PRIVATE_KEY>\nend"),
        ("card 4111 1111 1111 1111 declined", "card <CARD> declined"),
        ("card 4111-1111-1111-1111 declined", "card <CARD> declined"),
        ("card 4111111111111111", "card <CARD>"),
    ],
)
def test_redacts_typed_values(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_truncated_private_key_is_removed_to_the_end() -> None:
    text = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n"

    assert redact(f"before\n{text}") == "before\n<PRIVATE_KEY>"


@pytest.mark.parametrize(
    "text",
    [
        "assert 90 == 95",
        "version 1.2.3 at tests/test_cart.py:21",
        "order 4111111111111112 not found",  # 16 digits that fail the Luhn check
        "timestamp 2026-01-01T00:00:00+00:00",
        "",
    ],
)
def test_leaves_ordinary_text_alone(text: str) -> None:
    assert redact(text) == text
