import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Each condition breaks exactly one thing the wallet depends on. Everything stays on
# loopback or in a temp dir, so no case needs the outside world.
CONDITIONS = ("service_down", "dns_failure", "timeout", "missing_env_var", "read_only_dir")

SLOW_RESPONSE = 2.0


def _ledger_handler(delay: float) -> type[BaseHTTPRequestHandler]:
    class Ledger(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(delay)
            body = b'{"id": "ledger-1"}'
            self.send_response(201)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Ledger


@contextmanager
def lab_environment(root: Path, condition: str | None) -> Iterator[dict[str, str]]:
    """Environment variables for a wallet test run, healthy unless a condition breaks it."""
    statements = root / "statements"
    statements.mkdir(parents=True, exist_ok=True)
    delay = SLOW_RESPONSE if condition == "timeout" else 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ledger_handler(delay))
    server.daemon_threads = True
    port = server.server_address[1]

    env = {"WALLET_LEDGER_URL": f"http://127.0.0.1:{port}/entries"}
    env["WALLET_STATEMENTS_DIR"] = str(statements)
    if condition == "dns_failure":
        env["WALLET_LEDGER_URL"] = "http://ledger.invalid/entries"
    elif condition == "missing_env_var":
        del env["WALLET_LEDGER_URL"]
    elif condition == "read_only_dir":
        statements.chmod(0o555)

    if condition == "service_down":
        server.server_close()
    else:
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
    try:
        yield env
    finally:
        if condition != "service_down":
            server.shutdown()
            server.server_close()
        statements.chmod(0o755)
