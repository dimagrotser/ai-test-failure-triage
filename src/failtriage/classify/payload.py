def truncate_lines(text: str, head: int, tail: int) -> str:
    """Keep the first `head` and last `tail` lines, marking how many were cut."""
    lines = text.splitlines()
    omitted = len(lines) - head - tail
    if omitted <= 0:
        return text
    marker = f"... {omitted} lines omitted ..."
    return "\n".join([*lines[:head], marker, *lines[len(lines) - tail :]])


def truncate_message(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... {len(text) - limit} characters omitted"


def select_hunks(diff: str, trace: str, limit: int) -> str:
    """Keep the diff of files whose path appears in the trace, at most `limit` lines."""
    relevant = [f for f in _split_files(diff) if _path(f) in trace]
    kept = [line for file_diff in relevant for line in file_diff]
    return truncate_lines("\n".join(kept), head=limit, tail=0)


def _split_files(diff: str) -> list[list[str]]:
    files: list[list[str]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files.append([])
        if files:
            files[-1].append(line)
    return files


def _path(file_diff: list[str]) -> str:
    # `diff --git a/<path> b/<path>`: the b side is the file as it is after the change.
    return file_diff[0].rsplit(" b/", 1)[-1]
