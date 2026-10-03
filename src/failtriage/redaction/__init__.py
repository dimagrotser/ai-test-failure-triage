from failtriage.redaction.patterns import RULES


def redact(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text
