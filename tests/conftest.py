import socket
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regular tests replay recordings, so any real connection is a bug."""

    def refuse(self: socket.socket, address: Any) -> None:
        raise RuntimeError(f"network access in a test: {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
