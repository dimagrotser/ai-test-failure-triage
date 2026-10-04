import socket
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regular tests replay recordings, so any real connection is a bug."""

    def refuse(self: socket.socket, address: Any) -> None:
        raise RuntimeError(f"network access in a test: {address}")

    def refuse_ex(self: socket.socket, address: Any) -> int:
        raise RuntimeError(f"network access in a test: {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_ex)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in the developer's shell must not turn a heuristics test into a live run."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
