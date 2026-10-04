import time
from collections.abc import Callable
from typing import Any

import httpx

API_URL = "https://api.github.com"
# Waiting longer than this inside a CI step costs more than it saves.
MAX_WAIT_SECONDS = 60


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url
        self._sleep = sleep
        self._max_retries = max_retries
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=30.0,
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def get_json(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        """One JSON object from a GET endpoint."""
        body = self._get(path, params).json()
        if not isinstance(body, dict):
            raise GitHubError("GitHub did not answer with an object")
        return body

    def get_bytes(self, path: str) -> bytes:
        """A raw download such as an artifact zip. The redirect to storage drops the token."""
        return self._get(path, None).content

    def get_pages(self, path: str) -> list[dict[str, Any]]:
        """Items of a list endpoint, following `Link: rel=next` to the last page."""
        items: list[dict[str, Any]] = []
        url: str | None = path
        params: dict[str, str | int] | None = {"per_page": 100}
        while url:
            response = self._get(url, params)
            body = response.json()
            if not isinstance(body, list):
                raise GitHubError("GitHub did not answer with a list")
            items.extend(body)
            url = response.links.get("next", {}).get("url")
            params = None
            if url and not self._is_api_url(url):
                # The token rides on every request, so it must not follow a link off the API.
                raise GitHubError("GitHub pagination points to another host")
        return items

    def _is_api_url(self, url: str) -> bool:
        # A prefix check would accept `api.github.com.evil.example` and `api.github.com@evil`.
        target, api = httpx.URL(url), httpx.URL(self._base_url)
        return (target.scheme, target.host, target.port) == (api.scheme, api.host, api.port)

    def _get(self, url: str, params: dict[str, str | int] | None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                # Only the type: the message can quote the URL and headers.
                raise GitHubError(f"request to GitHub failed: {type(exc).__name__}") from None
            if response.is_success:
                return response
            wait = _rate_limit_wait(response)
            if wait is None:
                raise GitHubError(f"GitHub answered {response.status_code}")
            if attempt == self._max_retries or wait > MAX_WAIT_SECONDS:
                raise GitHubError(f"GitHub rate limit, answered {response.status_code}")
            self._sleep(wait)
        raise AssertionError("unreachable")


def _rate_limit_wait(response: httpx.Response) -> float | None:
    """Seconds to wait when the answer is a rate limit, None for any other failure."""
    if response.status_code not in (403, 429):
        return None
    retry_after = response.headers.get("Retry-After", "")
    # Retry-After may also be an HTTP date, which is not worth parsing: treat it as a plain error.
    if retry_after.isdigit():
        return float(retry_after)
    if response.headers.get("X-RateLimit-Remaining") == "0":
        reset = float(response.headers.get("X-RateLimit-Reset", "0"))
        return max(reset - time.time(), 0.0)
    return None
