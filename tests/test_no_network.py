import socket

import pytest


def test_opening_a_network_connection_fails_in_tests() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="network"):
        sock.connect(("127.0.0.1", 9))


def test_connect_ex_is_blocked_too() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="network"):
        sock.connect_ex(("127.0.0.1", 9))
