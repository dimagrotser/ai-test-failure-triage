# Group failures deterministically before calling the LLM

Forty failures with one cause should cost one LLM call and produce one line in the report. Grouping by exact signature is plain code, so it is testable and free. The price is that one cause can split into several groups when messages differ; I accept that over merging unrelated causes, which would hide real problems.
