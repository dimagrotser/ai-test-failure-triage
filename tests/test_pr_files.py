import json
from pathlib import Path

import httpx

from failtriage.github.client import GitHubClient
from failtriage.github.pr_files import ChangedFile, list_pr_files, to_unified_diff

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _files() -> list[ChangedFile]:
    raw = json.loads((FIXTURES / "pulls_files_page1.json").read_text(encoding="utf-8"))
    raw += json.loads((FIXTURES / "pulls_files_page2.json").read_text(encoding="utf-8"))
    return [ChangedFile.model_validate(f) for f in raw]


def test_lists_files_of_the_base_repository_without_a_checkout() -> None:
    # A fork PR is read from the base repo, where the token has access, never from the fork.
    seen: list[str] = []
    page = (FIXTURES / "pulls_files_page1.json").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=page, headers={"Content-Type": "application/json"})

    client = GitHubClient("t", transport=httpx.MockTransport(handler))

    files = list_pr_files(client, "acme/wallet", 7)

    assert seen == ["/repos/acme/wallet/pulls/7/files"]
    assert [f.filename for f in files] == ["src/wallet/fees.py"]


def test_a_file_without_a_patch_is_kept_with_none() -> None:
    logo = next(f for f in _files() if f.filename == "assets/logo.png")

    assert logo.patch is None


def test_the_unified_diff_has_a_header_and_the_patch_per_file() -> None:
    diff = to_unified_diff(_files()[:1])

    assert diff.startswith("diff --git a/src/wallet/fees.py b/src/wallet/fees.py\n")
    assert "--- a/src/wallet/fees.py\n+++ b/src/wallet/fees.py\n@@ -10,7 +10,8 @@" in diff
    assert "+    fee = amount * rate" in diff


def test_a_file_without_a_patch_gets_only_its_header() -> None:
    logo = [f for f in _files() if f.filename == "assets/logo.png"]

    assert to_unified_diff(logo) == "diff --git a/assets/logo.png b/assets/logo.png"


def test_a_renamed_file_names_both_sides() -> None:
    renamed = [f for f in _files() if f.status == "renamed"]

    diff = to_unified_diff(renamed)

    assert diff.startswith("diff --git a/tests/test_caps.py b/tests/test_limits.py\n")
    assert "--- a/tests/test_caps.py\n+++ b/tests/test_limits.py\n" in diff
