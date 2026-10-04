import io
import zipfile
from typing import NamedTuple

from pydantic import BaseModel

from failtriage.github.client import GitHubClient, GitHubError
from failtriage.history import HistoryEntry, HistoryError, parse_history

ARTIFACT_NAME = "failtriage-history"
ARTIFACT_FILE = "history.json"
MAIN_BRANCH = "main"
# A history file is a few hundred bytes per run, so anything bigger is not ours.
MAX_FILE_BYTES = 10 * 1024 * 1024


class _WorkflowRun(BaseModel):
    head_branch: str | None = None


class _Artifact(BaseModel):
    id: int
    expired: bool
    workflow_run: _WorkflowRun | None = None


class FetchedHistory(NamedTuple):
    entries: list[HistoryEntry]
    skipped: list[int]


def fetch_history(client: GitHubClient, repo: str, runs: int) -> FetchedHistory:
    """Merge the history artifacts of the last `runs` runs on main, newest first.

    An artifact that is gone or unreadable is skipped and reported by id. Only a failing
    listing raises, since then there is nothing to merge."""
    listing = client.get_json(
        f"/repos/{repo}/actions/artifacts", {"name": ARTIFACT_NAME, "per_page": 100}
    )
    artifacts = [_Artifact.model_validate(a) for a in listing.get("artifacts", [])]
    recent = [a for a in artifacts if _on_main(a) and not a.expired][:runs]
    entries: list[HistoryEntry] = []
    skipped: list[int] = []
    for artifact in recent:
        try:
            entries.extend(_read_artifact(client, repo, artifact.id))
        except (GitHubError, HistoryError, zipfile.BadZipFile, KeyError, ValueError):
            skipped.append(artifact.id)
    return FetchedHistory(entries, skipped)


def _on_main(artifact: _Artifact) -> bool:
    return artifact.workflow_run is not None and artifact.workflow_run.head_branch == MAIN_BRANCH


def _read_artifact(client: GitHubClient, repo: str, artifact_id: int) -> list[HistoryEntry]:
    data = client.get_bytes(f"/repos/{repo}/actions/artifacts/{artifact_id}/zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo(ARTIFACT_FILE).file_size > MAX_FILE_BYTES:
            raise ValueError("history file too large")
        return parse_history(archive.read(ARTIFACT_FILE))
