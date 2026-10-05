import json
from pathlib import Path

import httpx
import pytest

from failtriage.github.client import GitHubClient, GitHubError

FIXTURES = Path(__file__).parent / "fixtures" / "github"
PAGE2_URL = "https://api.github.com/repositories/1/pulls/7/files?per_page=100&page=2"


def _page(name: str) -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _client(
    handler: httpx.MockTransport | None = None,
    sleeps: list[float] | None = None,
    max_retries: int = 3,
) -> GitHubClient:
    return GitHubClient(
        "ghs_exampletoken",
        transport=handler,
        sleep=(sleeps if sleeps is not None else []).append,
        max_retries=max_retries,
    )


def test_follows_the_next_link_and_joins_pages() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=_page("pulls_files_page2.json"))
        link = f'<{PAGE2_URL}>; rel="next", <{PAGE2_URL}>; rel="last"'
        return httpx.Response(200, json=_page("pulls_files_page1.json"), headers={"Link": link})

    items = _client(httpx.MockTransport(handler)).get_pages("/repos/acme/wallet/pulls/7/files")

    assert [i["filename"] for i in items] == [
        "src/wallet/fees.py",
        "assets/logo.png",
        "tests/test_limits.py",
    ]
    assert len(seen) == 2
    assert "per_page=100" in seen[0]


def test_sends_the_token_and_the_api_version() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    _client(httpx.MockTransport(handler)).get_pages("/repos/acme/wallet/pulls/7/files")

    assert requests[0].headers["Authorization"] == "Bearer ghs_exampletoken"
    assert requests[0].headers["Accept"] == "application/vnd.github+json"
    assert "X-GitHub-Api-Version" in requests[0].headers


def test_waits_for_retry_after_and_tries_again() -> None:
    answers = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=[{}])]
    sleeps: list[float] = []

    items = _client(httpx.MockTransport(lambda r: answers.pop(0)), sleeps).get_pages("/x")

    assert items == [{}]
    assert sleeps == [7]


def test_retries_a_secondary_rate_limit_403() -> None:
    limited = httpx.Response(
        403,
        headers={"Retry-After": "3"},
        json={"message": "You have exceeded a secondary rate limit."},
    )
    answers = [limited, httpx.Response(200, json=[])]
    sleeps: list[float] = []

    _client(httpx.MockTransport(lambda r: answers.pop(0)), sleeps).get_pages("/x")

    assert sleeps == [3]


def test_waits_until_the_primary_limit_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("failtriage.github.client.time.time", lambda: 1000.0)
    limited = httpx.Response(
        403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1012"}
    )
    answers = [limited, httpx.Response(200, json=[])]
    sleeps: list[float] = []

    _client(httpx.MockTransport(lambda r: answers.pop(0)), sleeps).get_pages("/x")

    assert sleeps == [12]


def test_gives_up_after_the_retry_budget() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(GitHubError, match="rate limit"):
        _client(httpx.MockTransport(handler), max_retries=2).get_pages("/x")

    assert calls == 3


def test_does_not_wait_for_a_reset_that_is_too_far_away() -> None:
    sleeps: list[float] = []
    limited = httpx.Response(429, headers={"Retry-After": "3600"})

    with pytest.raises(GitHubError, match="rate limit"):
        _client(httpx.MockTransport(lambda r: limited), sleeps).get_pages("/x")

    assert sleeps == []


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_errors_carry_the_status_and_nothing_else(status: int) -> None:
    body = {"message": "Bad credentials for ghs_exampletoken", "documentation_url": "x"}
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))

    with pytest.raises(GitHubError) as caught:
        _client(transport).get_pages("/repos/acme/wallet/pulls/7/files")

    assert str(status) in str(caught.value)
    assert "ghs_exampletoken" not in str(caught.value)
    assert "Bad credentials" not in str(caught.value)


def test_a_transport_failure_becomes_a_github_error_without_its_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not reach https://api.github.com with ghs_exampletoken")

    with pytest.raises(GitHubError) as caught:
        _client(httpx.MockTransport(handler)).get_pages("/x")

    assert "ghs_exampletoken" not in str(caught.value)


def test_a_body_that_is_not_a_list_is_an_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"message": "hi"}))

    with pytest.raises(GitHubError, match="list"):
        _client(transport).get_pages("/x")


def test_does_not_follow_a_next_link_to_another_host() -> None:
    link = '<https://evil.example/steal?page=2>; rel="next"'
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[], headers={"Link": link}))

    with pytest.raises(GitHubError, match="another host"):
        _client(transport).get_pages("/x")


@pytest.mark.parametrize(
    "target",
    [
        "https://api.github.com.evil.example/steal?page=2",
        "https://api.github.com@evil.example/steal?page=2",
        "http://api.github.com/steal?page=2",
    ],
)
def test_a_next_link_that_only_looks_like_the_api_is_refused(target: str) -> None:
    link = f'<{target}>; rel="next"'
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[], headers={"Link": link}))

    with pytest.raises(GitHubError, match="another host"):
        _client(transport).get_pages("/x")


def test_a_retry_after_date_is_not_a_crash() -> None:
    date = "Wed, 21 Oct 2026 07:28:00 GMT"
    transport = httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": date}))

    with pytest.raises(GitHubError, match="GitHub answered 429"):
        _client(transport).get_pages("/x")


def test_get_json_returns_an_object_and_passes_the_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"total_count": 0, "artifacts": []})

    body = _client(httpx.MockTransport(handler)).get_json(
        "/repos/acme/wallet/actions/artifacts", {"name": "failtriage-history", "per_page": 100}
    )

    assert body == {"total_count": 0, "artifacts": []}
    assert seen[0].url.params["name"] == "failtriage-history"
    assert seen[0].url.params["per_page"] == "100"


def test_get_json_rejects_a_list() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))

    with pytest.raises(GitHubError, match="object"):
        _client(transport).get_json("/x")


def test_get_bytes_returns_the_body_and_retries_a_rate_limit() -> None:
    answers = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, content=b"PK"),
    ]
    sleeps: list[float] = []

    data = _client(httpx.MockTransport(lambda r: answers.pop(0)), sleeps).get_bytes("/x")

    assert data == b"PK"
    assert sleeps == [2]


def test_get_bytes_raises_on_a_missing_artifact() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(410, json={"message": "expired"}))

    with pytest.raises(GitHubError, match="410"):
        _client(transport).get_bytes("/x")


def test_the_token_does_not_follow_a_redirect_to_another_host() -> None:
    # Artifact downloads answer 302 to blob storage.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://blob.example.net/zip?sig=1"})
        return httpx.Response(200, content=b"PK")

    data = _client(httpx.MockTransport(handler)).get_bytes(
        "/repos/acme/wallet/actions/artifacts/9/zip"
    )

    assert data == b"PK"
    assert [r.url.host for r in seen] == ["api.github.com", "blob.example.net"]
    assert "Authorization" not in seen[1].headers
