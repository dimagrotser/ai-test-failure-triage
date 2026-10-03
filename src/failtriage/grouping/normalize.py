import re

# Order matters: dates, UUIDs and paths hold digits that the number rule would split apart.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
        ),
        "<DATE>",
    ),
    (re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE), "<UUID>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE), "<HEX>"),
    (re.compile(r"""file://[^\s'")]+"""), "<PATH>"),
    (re.compile(r"""\b[A-Za-z]:\\(?:[^\\\s'"]+\\)*[^\\\s'"]*"""), "<PATH>"),
    # A path starts after whitespace, a quote or a bracket, so `and/or`, `1/2` and URLs stay.
    (re.compile(r"(?<![\w<>/.:-])/(?:[\w.@+-]+/)*[\w.@+-]+"), "<PATH>"),
    # An apostrophe inside a word never opens a literal.
    (re.compile(r"""(?<!\w)(?P<q>['"])(?:(?!(?P=q))[^\r\n]){0,200}(?P=q)"""), "<STR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<NUM>"),
]


def normalize(text: str) -> str:
    """Replace values that change between runs so equal causes produce equal text."""
    for pattern, placeholder in _RULES:
        text = pattern.sub(placeholder, text)
    return text.strip()
