# Redact right after parsing, and check again before the LLM call

Redaction runs on every text field immediately after parsing, so grouping keys, reports and prompts only ever see redacted text. Replacements are typed and stable (`<EMAIL>`, `<JWT>`, `<SECRET>`), which keeps signatures deterministic. Values in env dumps are removed by key name. I skipped entropy-based detection because it is noisy and hard to test. As a second layer, the final LLM payload is scanned with the same patterns and the call is aborted on any match instead of being sent.
