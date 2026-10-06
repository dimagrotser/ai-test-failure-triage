# Real cases are labeled by hand and redacted before they touch the disk

A real failure has no wallet to patch, so ADR 0004's counterfactual cannot apply. I accept that for the `real` source and keep those cases apart in the eval output, so a wrong label shows up in one row and does not blur the verified ones. The label is filled in by a person, never by an LLM, and `make lab` refuses a case until it is.

The import command redacts the report as XML values, not as raw text, because a typed placeholder such as `<SECRET>` is not valid inside XML text and an escaped secret such as `&quot;` would hide from the text rules. It then runs the same pass again and writes nothing if the second pass changes anything, which is the second layer of ADR 0003. `make lab` repeats the check on every real case, so a file edited after the import cannot slip a secret into the dataset. Real cases live in `evals/lab/real/` because `make lab` rebuilds `evals/cases/` from scratch.
