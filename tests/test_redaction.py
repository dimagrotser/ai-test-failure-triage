import pytest

from failtriage.models import Attempt, Status, TestResult
from failtriage.redaction import redact, redact_result

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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DB_PASSWORD=hunter2", "DB_PASSWORD=<SECRET>"),
        ("STRIPE_API_KEY=sk_live_abc123", "STRIPE_API_KEY=<SECRET>"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "AWS_SECRET_ACCESS_KEY=<SECRET>"),
        ("GITHUB_TOKEN=notaprefixedvalue", "GITHUB_TOKEN=<SECRET>"),
        ("export SERVICE_SECRET='two words'", "export SERVICE_SECRET=<SECRET>"),
        ("password: s3cret!", "password: <SECRET>"),
        ('{"password": "s3cret", "user": "bob"}', '{"password": <SECRET>, "user": "bob"}'),
        ("env={'APP_TOKEN': 'abc', 'MODE': 'test'}", "env={'APP_TOKEN': <SECRET>, 'MODE': 'test'}"),
        ("api_key=abc&page=2", "api_key=<SECRET>&page=2"),
        ("db_password = x", "db_password = <SECRET>"),
    ],
)
def test_removes_env_values_by_key_name(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_typed_placeholder_is_not_overwritten_by_key_rule() -> None:
    text = "API_TOKEN=ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

    assert redact(text) == "API_TOKEN=<TOKEN>"


def test_keeps_values_of_ordinary_keys() -> None:
    text = "DEBUG=1 MODE=test HOSTNAME=ci-runner-3 KEYBOARD=us"

    assert redact(text) == text


def test_redaction_is_idempotent() -> None:
    text = f"DB_PASSWORD=x {JWT} jane@example.com 4111111111111111\nCookie: a=b\n{PRIVATE_KEY}"

    once = redact(text)

    assert redact(once) == once


def test_redact_result_cleans_every_text_field_of_every_attempt() -> None:
    attempts = [
        Attempt(
            status=Status.FAILED,
            message="mail jane@example.com",
            stack_trace="DB_PASSWORD=x",
            stdout="Cookie: a=b",
            stderr=f"jwt {JWT}",
            duration=1.5,
        ),
        Attempt(status=Status.PASSED),
    ]
    result = TestResult(test_id="t.py::a", status=Status.PASSED_ON_RETRY, attempts=attempts)

    redacted = redact_result(result)

    assert redacted.test_id == "t.py::a"
    assert redacted.status is Status.PASSED_ON_RETRY
    first, second = redacted.attempts
    assert first.message == "mail <EMAIL>"
    assert first.stack_trace == "DB_PASSWORD=<SECRET>"
    assert first.stdout == "Cookie: <SECRET>"
    assert first.stderr == "jwt <JWT>"
    assert first.duration == 1.5
    assert second == Attempt(status=Status.PASSED)
    assert result.attempts[0].message == "mail jane@example.com"
