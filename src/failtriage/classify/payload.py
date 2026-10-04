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
