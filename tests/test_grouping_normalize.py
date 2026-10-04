import pytest

from failtriage.grouping.normalize import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("failed at 2026-10-03", "failed at <DATE>"),
        ("failed at 2026-10-03T11:42:07", "failed at <DATE>"),
        ("failed at 2026-10-03 11:42:07.113204", "failed at <DATE>"),
        ("failed at 2026-10-03T11:42:07.5+02:00", "failed at <DATE>"),
        ("failed at 2026-10-03T11:42:07Z", "failed at <DATE>"),
        ("order 3f2b8c1e-9d4a-4e7b-8a21-5c6d7e8f9a0b missing", "order <UUID> missing"),
        ("order 3F2B8C1E-9D4A-4E7B-8A21-5C6D7E8F9A0B missing", "order <UUID> missing"),
        ("<Account object at 0x7f3a1c2b5d90>", "<Account object at <HEX>>"),
        ("cannot open /tmp/lab/run-42/out.csv", "cannot open <PATH>"),
        ("cannot open C:\\Users\\ci\\work\\out.csv", "cannot open <PATH>"),
        ("cannot load file:///home/ci/app/main.js", "cannot load <PATH>"),
        ("Permission denied: '/tmp/lab/a.csv'", "Permission denied: <STR>"),
        ("no element with data-testid='amount'", "no element with data-testid=<STR>"),
        ('expected "ready" but got "idle"', "expected <STR> but got <STR>"),
        ("[Errno 61] Connection refused", "[Errno <NUM>] Connection refused"),
        ("assert 90 == 95", "assert <NUM> == <NUM>"),
        ("fee 1.49 is not 1.50", "fee <NUM> is not <NUM>"),
        ("Timeout 30000ms exceeded", "Timeout <NUM>ms exceeded"),
        ("took 1.5s, then 120ms", "took <NUM>s, then <NUM>ms"),
        ("connect to 10.0.0.1 failed", "connect to <NUM> failed"),
    ],
)
def test_normalize_replaces_volatile_values(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_a_date_is_one_placeholder_not_a_run_of_numbers() -> None:
    assert normalize("at 2026-10-03T11:42:07") == "at <DATE>"


def test_a_uuid_with_digits_is_one_placeholder_not_numbers_and_letters() -> None:
    assert normalize("id 12345678-1234-1234-1234-123456789012") == "id <UUID>"


def test_a_path_with_digits_is_one_placeholder() -> None:
    assert normalize("open /var/run/42/7/x.log") == "open <PATH>"


def test_a_quoted_path_is_one_placeholder() -> None:
    assert normalize("open '/var/run/42/x.log'") == "open <STR>"


def test_digits_inside_identifiers_stay() -> None:
    assert normalize("test_v2 raised utf8 error in md5sum") == "test_v2 raised utf8 error in md5sum"


def test_apostrophes_in_words_do_not_open_a_literal() -> None:
    assert normalize("can't find 'x' and isn't 'y'") == "can't find <STR> and isn't <STR>"


def test_urls_and_relative_paths_stay() -> None:
    text = "GET http://staging.internal/api/v1 from tests/test_api.py"

    assert normalize(text) == text


def test_redaction_placeholders_stay() -> None:
    assert normalize("owner <EMAIL> token=<SECRET>") == "owner <EMAIL> token=<SECRET>"


def test_normalize_is_idempotent() -> None:
    once = normalize("id 3f2b8c1e-9d4a-4e7b-8a21-5c6d7e8f9a0b at /tmp/x 'a' 5")

    assert normalize(once) == once


def test_the_same_failure_on_two_machines_normalizes_the_same() -> None:
    mac = "<urlopen error [Errno 61] Connection refused>"
    linux = "<urlopen error [Errno 111] Connection refused>"

    assert normalize(mac) == normalize(linux)
