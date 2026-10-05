import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from failtriage.github.client import GitHubClient, GitHubError
from failtriage.github.history_artifacts import fetch_history

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _entry(run_id: int, status: str = "passed") -> dict[str, object]:
    return {
        "test_id": "tests/test_fees.py::test_fee",
        "status": status,
        "attempts": 1,
        "sha": f"{run_id:040d}",
        "run_id": run_id,
    }


def _zip(content: bytes, name: str = "history.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


def _history_zip(run_id: int) -> bytes:
    return _zip(json.dumps([_entry(run_id)]).encode())


class FakeGitHub:
    """Serves the recorded artifact listing and one zip per artifact id."""

    def __init__(self, zips: dict[int, bytes | int]) -> None:
        self.zips = zips
        self.requests: list[httpx.Request] = []

    def client(self) -> GitHubClient:
        return GitHubClient("t", transport=httpx.MockTransport(self._handle), sleep=lambda s: None)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/repos/acme/wallet/actions/artifacts":
            listing = (FIXTURES / "actions_artifacts.json").read_text(encoding="utf-8")
            return httpx.Response(200, text=listing, headers={"Content-Type": "application/json"})
        artifact_id = int(path.split("/")[-2])
        answer = self.zips[artifact_id]
        if isinstance(answer, int):
            return httpx.Response(answer, json={"message": "gone"})
        return httpx.Response(200, content=answer)


def _downloaded(github: FakeGitHub) -> list[int]:
    return [int(r.url.path.split("/")[-2]) for r in github.requests if r.url.path.endswith("/zip")]


def test_merges_the_newest_runs_on_main() -> None:
    github = FakeGitHub(
        {5003: _history_zip(9003), 5002: _history_zip(9002), 5000: _history_zip(9000)}
    )

    result = fetch_history(github.client(), "acme/wallet", 3)

    assert [e.run_id for e in result.entries] == [9003, 9002, 9000]
    assert result.skipped == []


def test_expired_artifacts_and_other_branches_are_not_downloaded() -> None:
    github = FakeGitHub(
        {5003: _history_zip(9003), 5002: _history_zip(9002), 5000: _history_zip(9000)}
    )

    fetch_history(github.client(), "acme/wallet", 10)

    assert _downloaded(github) == [5003, 5002, 5000]


def test_only_the_last_n_runs_are_downloaded() -> None:
    github = FakeGitHub({5003: _history_zip(9003), 5002: _history_zip(9002)})

    result = fetch_history(github.client(), "acme/wallet", 2)

    assert _downloaded(github) == [5003, 5002]
    assert {e.run_id for e in result.entries} == {9003, 9002}


def test_a_missing_artifact_is_skipped_and_the_rest_is_merged() -> None:
    github = FakeGitHub({5003: 410, 5002: _history_zip(9002), 5000: 404})

    result = fetch_history(github.client(), "acme/wallet", 3)

    assert [e.run_id for e in result.entries] == [9002]
    assert result.skipped == [5003, 5000]


def test_a_broken_artifact_is_skipped() -> None:
    broken: dict[int, bytes | int] = {
        5003: b"not a zip",
        5002: _zip(b"[]", name="other.json"),
        5000: _zip(b'[{"test_id": "t"}]'),
    }

    result = fetch_history(FakeGitHub(broken).client(), "acme/wallet", 3)

    assert result.entries == []
    assert result.skipped == [5003, 5002, 5000]


def test_an_oversized_history_file_is_skipped() -> None:
    padding = b" " * (10 * 1024 * 1024 + 1)
    github = FakeGitHub({5003: _zip(b"[]" + padding), 5002: _history_zip(9002)})

    result = fetch_history(github.client(), "acme/wallet", 2)

    assert result.skipped == [5003]
    assert [e.run_id for e in result.entries] == [9002]


def test_a_cold_start_has_no_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 0, "artifacts": []})

    client = GitHubClient("t", transport=httpx.MockTransport(handler))

    result = fetch_history(client, "acme/wallet", 10)

    assert result.entries == []
    assert result.skipped == []


def test_a_main_branch_of_a_fork_is_not_history() -> None:
    # 5006 is a run on a fork's `main`: it is in the listing but must never be downloaded.
    github = FakeGitHub(
        {5003: _history_zip(9003), 5002: _history_zip(9002), 5000: _history_zip(9000)}
    )

    result = fetch_history(github.client(), "acme/wallet", 10)

    assert 5006 not in _downloaded(github)
    assert 9006 not in {e.run_id for e in result.entries}


def test_the_listing_asks_for_the_history_artifact_by_name() -> None:
    github = FakeGitHub(
        {5003: _history_zip(9003), 5002: _history_zip(9002), 5000: _history_zip(9000)}
    )

    fetch_history(github.client(), "acme/wallet", 3)

    assert github.requests[0].url.params["name"] == "failtriage-history"
    assert github.requests[0].url.params["per_page"] == "100"


def test_every_request_is_a_get_on_the_actions_api() -> None:
    # Reading artifacts is covered by the `actions: read` permission and nothing else.
    github = FakeGitHub(
        {5003: _history_zip(9003), 5002: _history_zip(9002), 5000: _history_zip(9000)}
    )

    fetch_history(github.client(), "acme/wallet", 10)

    assert {r.method for r in github.requests} == {"GET"}
    assert all(r.url.path.startswith("/repos/acme/wallet/actions/") for r in github.requests)


def test_a_failing_listing_raises_github_error() -> None:
    client = GitHubClient("t", transport=httpx.MockTransport(lambda r: httpx.Response(403)))

    with pytest.raises(GitHubError, match="403"):
        fetch_history(client, "acme/wallet", 10)


def test_a_zip_that_declares_a_small_file_but_inflates_is_skipped() -> None:
    bomb = _zip(b"[]" + b" " * (10 * 1024 * 1024 + 1))
    buffer = bytearray(bomb)
    # Rewrite the declared uncompressed size in the local and central headers to a small value.
    declared = (10 * 1024 * 1024 + 3).to_bytes(4, "little")
    while (at := bytes(buffer).find(declared)) != -1:
        buffer[at : at + 4] = (2).to_bytes(4, "little")
    github = FakeGitHub({5003: bytes(buffer), 5002: _history_zip(9002)})

    result = fetch_history(github.client(), "acme/wallet", 2)

    assert result.skipped == [5003]
    assert [e.run_id for e in result.entries] == [9002]
