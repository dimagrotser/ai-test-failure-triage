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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pushing with ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "pushing with <TOKEN>"),
        ("github_pat_" + "11ABCDEFG0abcdefghij_klmnopqrstuvwxyz0123", "<TOKEN>"),
        ("using AKIAIOSFODNN7EXAMPLE here", "using <TOKEN> here"),
        ("xo" + "xb-123456789012-abcdefghijklmnop failed", "<TOKEN> failed"),
        ("sk-ant-api03-" + "abcdefghijklmnopqrstuvwxyz0123", "<TOKEN>"),
        ("Authorization: Bearer abc.def", "Authorization: <SECRET>"),
        ("authorization: Basic dXNlcjpwYXNz\nnext line", "authorization: <SECRET>\nnext line"),
        ("Cookie: session=abc123; theme=dark\nHost: x", "Cookie: <SECRET>\nHost: x"),
        ("Set-Cookie: sid=1; HttpOnly", "Set-Cookie: <SECRET>"),
        (
            "headers={'Authorization': 'Bearer abc', 'Accept': 'json'}",
            "headers={'Authorization': <SECRET>, 'Accept': 'json'}",
        ),
        (
            "connect postgres://app:hunter2@db.internal:5432/wallet",
            "connect postgres://app:<SECRET>@db.internal:5432/wallet",
        ),
        (
            "GET https://user:p%40ss@host/path failed",
            "GET https://user:<SECRET>@host/path failed",
        ),
    ],
)
def test_redacts_tokens_headers_and_url_passwords(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_url_without_password_is_kept() -> None:
    text = "GET https://api.example.com:8443/v1/rates failed"

    assert redact(text) == text
