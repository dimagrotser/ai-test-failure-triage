from decimal import Decimal
from html.parser import HTMLParser

import pytest

from wallet.accounts import Account
from wallet.receipt import render_receipt
from wallet.transfers import transfer


class _TextByTestId(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.texts: dict[str, str] = {}
        self._current: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current = dict(attrs).get("data-testid")

    def handle_data(self, data: str) -> None:
        if self._current:
            self.texts[self._current] = data

    def handle_endtag(self, tag: str) -> None:
        self._current = None


def text_by_testid(html: str, testid: str) -> str:
    parser = _TextByTestId()
    parser.feed(html)
    if testid not in parser.texts:
        raise LookupError(f"no element with data-testid='{testid}'")
    return parser.texts[testid]


@pytest.fixture
def receipt() -> str:
    source = Account("alice", Decimal("200.00"))
    target = Account("bob")
    transfer(source, target, Decimal("100.00"))
    return render_receipt(source, target, Decimal("100.00"))


def test_receipt_shows_the_amount(receipt: str) -> None:
    assert text_by_testid(receipt, "transfer-amount") == "100.00"


def test_receipt_shows_the_fee(receipt: str) -> None:
    assert text_by_testid(receipt, "transfer-fee") == "1.50"


def test_receipt_shows_the_sender_balance_after_the_transfer(receipt: str) -> None:
    assert text_by_testid(receipt, "sender-balance") == "98.50"
