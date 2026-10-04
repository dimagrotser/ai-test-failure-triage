from pydantic import BaseModel

from failtriage.github.client import GitHubClient


class ChangedFile(BaseModel):
    filename: str
    status: str
    previous_filename: str | None = None
    # GitHub leaves the patch out for binary files, pure renames and diffs that are too large.
    patch: str | None = None


def list_pr_files(client: GitHubClient, repo: str, pr: int) -> list[ChangedFile]:
    """Files of a pull request, read from the base repo so fork PRs need no checkout."""
    items = client.get_pages(f"/repos/{repo}/pulls/{pr}/files")
    return [ChangedFile.model_validate(item) for item in items]


def to_unified_diff(files: list[ChangedFile]) -> str:
    """The files as one git-style diff. A file without a patch keeps only its header."""
    return "\n".join(_file_diff(f) for f in files)


def _file_diff(file: ChangedFile) -> str:
    old = file.previous_filename or file.filename
    lines = [f"diff --git a/{old} b/{file.filename}"]
    if file.patch:
        lines += [f"--- a/{old}", f"+++ b/{file.filename}", file.patch.rstrip("\n")]
    return "\n".join(lines)
