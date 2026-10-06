# The failure lab

`evals/cases/` is the labeled dataset the evals run against. Nobody edits it by hand. `make lab` rebuilds all of it from `evals/lab/scenarios/`, and every case in it has a label that the build has checked.

```
make lab
```

The build runs every scenario against a small wallet app in `evals/lab/wallet/`, writes the cases into a staging directory and swaps it in for `evals/cases/` only when all of them pass. If one scenario fails its check, the build stops with a message naming it and `evals/cases/` stays as it was. A scenario you delete also disappears from the dataset on the next run.

Two runs on the same machine give byte-identical files. Timings, temp paths, ports and object addresses are normalized, pytest runs in a fixed environment, and flaky behavior depends on the attempt number, never on real randomness. The cases are not identical across operating systems: the error text of a refused connection contains an errno that differs between macOS and Linux. Rebuild on the machine you commit from.

## What a case contains

| File | Content |
| --- | --- |
| `junit.xml` | The failing run, as pytest wrote it |
| `diff.patch` | The change that broke the wallet |
| `history.json` | Earlier runs of the same tree, empty if the scenario says `history: none` |
| `label.yaml` | The ground-truth category, copied from `scenario.yaml` |

## Adding a scenario

1. Create `evals/lab/scenarios/<category>-<short-name>/`. The name becomes the case id.
2. Write `diff.patch`. Copy `evals/lab/wallet/` somewhere, make `git init` there, change it and save `git diff` as the patch. Paths must read `a/wallet/...` or `a/tests/...`.
3. Write `scenario.yaml`.
4. Run `make lab`.
5. Add a test to `tests/test_lab_build.py` that names the test which must fail and a phrase from its message.
6. Commit the scenario and the rebuilt `evals/cases/` together.

`scenario.yaml` needs four fields. `category` is one of `product_bug`, `test_bug`, `flaky`, `environment`, `unknown`. `source` is `injected` for everything written by hand and `mutation` for the cases `make lab-mutants` generates. `real` is not a scenario source, see below. `scenario` is one line saying what is wrong. `notes` explains why the label is right, and for `unknown` why nobody can tell. Some categories take one more field, listed below.

## What the build checks

[ADR 0004](adr/0004-lab-labels-by-counterfactual.md) has the reasoning. In short, a label is only written if the build can show the cause.

| Category | The patch may change | Check |
| --- | --- | --- |
| `product_bug` | `wallet/` only | The run fails. Reverting the patch turns it green. |
| `test_bug` | `tests/` only | The run fails. Reverting the patch turns it green. |
| `environment` | `wallet/` only | The run fails under `condition`. The same tree passes in a healthy environment. |
| `flaky` | `wallet/` and `tests/` | Fails without retries, passes on the first retry. Needs `kind`. |
| `unknown` | either | Fails and does not pass on retry. No counterfactual exists, by design. |

An `environment` scenario needs a `condition` from `evals/lab/environment.py`: `service_down`, `dns_failure`, `timeout`, `missing_env_var` or `read_only_dir`. A `flaky` scenario needs a `kind` that says where the nondeterminism comes from: `timing`, `randomness` or `order_dependence`. An `unknown` scenario can set `history: none` to model a cold start, with no earlier runs to compare against.

The build also rejects a patch that touches a directory its category must leave alone, and a wallet that already fails before the patch is applied.

## When a scenario is rejected

The message names the scenario and the check, for example `restoring the environment of environment-ledger-down does not turn the run green`. Fix the patch or the label. Do not loosen the check to make a scenario pass: a case with a label nobody verified is worse than no case.

## Mutation cases

Hand-written bugs reflect what I thought of. `mutmut` supplies bugs I did not choose: it mutates the wallet one small change at a time and runs the wallet tests against each mutant. A mutant the tests kill is a product bug by construction, because the app changed and the tests did not.

```
uv sync
make lab-mutants
```

This is optional and `make lab` does not need `mutmut`. The command runs `mutmut` on a copy of `evals/lab/wallet/` inside the same environment the cases use, so the ledger stub is up. It then writes `evals/lab/scenarios/product-bug-mutant-<module>-<function>/` with `source: mutation` and the mutant as `diff.patch`, and runs `make lab` to build the cases. Commit the scenarios and the rebuilt `evals/cases/` together. A second run on the same code produces no diff.

Only killed mutants are used. A surviving mutant is either equivalent (it changes nothing a test could see) or a gap in the wallet tests, and neither gives a failing run to label, so those are skipped. Timeouts and suspicious results are skipped too. Each candidate also has to pass the normal check: applied as a plain patch it fails the tests, and reverting it turns them green.

From each function the generator takes the killed mutant with the lowest number. The wallet has 7 mutated functions, so this gives 7 cases, far fewer than the mutants `mutmut` kills. The cases are a sample, and they follow whatever mutations `mutmut` happens to try: `quantize(None)` in the fee calculation is a real crash but not a bug anyone would write.

## Real cases

Injected and mutated bugs only cover what I thought of or what `mutmut` tried. A failing report from another project shows how the heuristics do on logs nobody wrote for them.

```
uv run python -m evals.lab.real path/to/junit.xml real-checkout-card
```

The command reads a JUnit XML report, redacts every text and attribute value in it and runs a second pass over the result. If that pass still changes anything, nothing is written. Only the redacted file is ever on disk, and the command prints the number of tests, the first message line of each Failure group and nothing else from the report. It refuses an existing case id, a report without failures, a report that declares XML entities and one with something secret-looking in a tag or attribute name, since those cannot be masked.

The case goes to `evals/lab/real/<id>/` with a `junit.xml` and a `label.yaml` whose `category`, `scenario` and `notes` fields are empty. Read the redacted `junit.xml`, decide the category yourself and fill in all of them. No LLM is involved, because a label that a model made up would only measure the model against itself. Redaction can miss things, so read the file for anything private before you commit it.

`make lab` copies the filled cases into `evals/cases/` next to the built ones and runs the redaction check on them again. A case stops the build if its category is empty or unknown, if `scenario` or `notes` is blank, or if redaction would still change its text.

A real case has no counterfactual, since the app is not here to revert ([ADR 0007](adr/0007-real-cases-labeled-by-hand.md)). Its label is my judgment, and the `real` row in the eval output keeps those groups apart from the verified ones. It has no `history.json`, and the importer only reads JUnit reports. A `diff.patch` can be added by hand next to `label.yaml`, in the redacted form, since the LLM gets the diff in the eval. `make lab` copies it after checking that redaction would not change it.

## Running the evals

```
uv run failtriage eval evals/
```

Without a key the command classifies every Failure group of every case with the heuristics only. It compares each answer with the label of the case and prints accuracy per category and per source, a confusion matrix and the list of misses. It also prints the share of groups the heuristics left as `unknown`, and how many of their `high` confidence answers were right. A case with several groups counts once per group, so `test-bug-fixture-leak` contributes four rows. A case that cannot be scored (no label, a missing or broken `junit.xml` or `history.json`, no failures at all) stops the run and is named in the message.

With `ANTHROPIC_API_KEY` set, the same cases are scored again for each model, by default `claude-sonnet-5-5` and `claude-haiku-4-5`. Pass `--model` once per model to pick others. Both configurations get the `history.json` of a case, and the LLM also gets `diff.patch`, the way a real run on a pull request would. Every payload is redacted before it is sent, as in `analyze`. A group whose call fails keeps the heuristics answer, and the output counts those so a flaky API cannot pass for a good model. Tokens and cost are printed for each model and in total, and `--json` writes everything to a file:

```
uv run failtriage eval evals/ --json evals/results/$(date -u +%F).json
```

This spends real money, so regular CI never runs it. The `eval` workflow does it on demand:

```
gh workflow run eval.yml
```

It needs the `ANTHROPIC_API_KEY` repository secret and fails without it. The tables go to the job summary and the JSON is uploaded as the `eval-results` artifact. I commit the JSON of a run to `evals/results/` by hand once I have read it.

Baseline on the current 38 cases, 48 groups. 40 groups have source `injected` and 8 have source `mutation`:

| Category | Groups | Correct |
|---|---|---|
| environment | 9 | 8 |
| flaky | 7 | 7 |
| unknown | 7 | 4 |
| product_bug | 16 | 7 |
| test_bug | 9 | 2 |

Overall 28 of 48, or 58%. By source, the injected groups score 23 of 40 and the mutation groups 5 of 8. The first `real` case, `real-preview-item-title`, came after this run. With it the heuristics score 28 of 49 and `real` is 0 of 1.

The mutation misses repeat the known weak spots. `product-bug-mutant-accounts-account-deposit` and `product-bug-mutant-transfers-transfer` change `<=` to `<` in the amount check, so a test fails with `DID NOT RAISE` in the test file and the rules call it a test bug, like `product-bug-funds-check-ignores-fee`. `product-bug-mutant-accounts-account-init` turns the owner into `None`, which shows up as an assertion mismatch in a test and stays `unknown`.

The weak spot is every assertion failure. A failing frame in test code together with an assertion mismatch is left undecided on purpose, because the test or the product could be wrong, so those groups come out as `unknown`. That accounts for `product-bug-fee-rounding`, `test-bug-expected-value` and three of the four groups of `test-bug-fixture-leak`. `product-bug-fee-rate-typo` is the same problem at a larger scale: one wrong constant produces four groups, and all four are assertion failures.

The rules also go wrong where the frame is not the place of the mistake, and the last batch added several of these:

- `product-bug-funds-check-ignores-fee` fails with `DID NOT RAISE` in the test file, so the rules call it a test bug. The broken check is in `wallet/transfers.py`.
- `test-bug-wrong-exception-expected` and `test-bug-receipt-fixture-short-of-funds` go the other way. The exception is raised inside the app, so the rules call them product bugs, but the fault is in what the test expects or sets up.
- `test-bug-statement-relative-path` reads as an environment problem because the error is a missing file.
- `environment-ledger-url-default` has no network wording in the ValueError about an empty url and the frame is in source code, so the rules call it a product bug.

Some `unknown` cases get a verdict they should not have. `unknown-amount-with-comma` raises from source code, so it comes out as a product bug. `unknown-confirmation-window-boundary` says "timed out" about the product's own 900 second rule, which the timeout pattern takes for a slow service. `unknown-account-tier-keyword` fails with a TypeError in the test file and is called a test bug, although the product could just as well have been meant to use the other name. `unknown-amount-formatting` and the two oldest unknown cases stay undecided, as they should.

The heuristics read the history of a case but never its diff, so the diff is where the LLM was expected to help. The first run of the `eval` workflow on 2026-10-06 is committed as `evals/results/2026-10-06.json`. It is only half a comparison. `claude-sonnet-5-5` classified all 48 groups. `claude-haiku-4-5` made no successful call, every group fell back to the heuristics, and its 28 of 48 only repeats the baseline. I have not found out why yet, so there is no Sonnet against Haiku result.

Sonnet scored 29 of 48, or 60%, against 28 of 48 for the heuristics alone. The injected groups went from 23 to 25 of 40 and the mutation groups from 5 to 4 of 8.

| Category | Groups | Heuristics | Sonnet |
|---|---|---|---|
| environment | 9 | 8 | 7 |
| flaky | 7 | 7 | 6 |
| unknown | 7 | 4 | 4 |
| product_bug | 16 | 7 | 7 |
| test_bug | 9 | 2 | 5 |

Compared group by group, Sonnet got 5 right that the heuristics missed and lost 4 that they had right, a net gain of 1. Four of the five gains are `test_bug` groups, and the fifth is `product-bug-fee-rate-typo`. I have not read the answers to see why, and the diff is a plausible reason. The losses are `environment-ledger-on-every-transfer` and `flaky-rates-cache-order`, which Sonnet called `product_bug`, and `product-bug-mutant-ledger-record-transfer` and `test-bug-fixture-leak`, which it called `unknown`. The flaky group has passed-on-retry evidence, which the rules read correctly. `product_bug` stayed at 7 of 16: Sonnet answered `unknown` for 9 of them, including `product-bug-fee-rounding`, so it is as cautious about assertion failures as the rules are. `environment-ledger-url-default` is wrong in both columns. Sonnet answered `unknown` for 17 groups (35%) and its `high` answers were right 14 times out of 16.

The run cost $0.35: 48 calls, 108,156 input tokens and 13,295 output tokens, about $0.007 and 2,250 input tokens per group. One run is one sample, and I did not repeat it, so I cannot say how much of the difference between 29 and 28 is noise. I read it as no clear gain overall.

Two cautions apply to any number in this section. The dataset is small, 48 groups, so one case moves a category by several points. And `real` has one case, so almost nothing here says how the classifier does on failures I did not write myself ([adoption.md](adoption.md)). The weak categories are `product_bug` (44%, with or without the LLM) and `test_bug` (22% for the heuristics, 56% with Sonnet), both because an assertion failure is left as `unknown` on purpose. The heuristics answered `high` for 7 groups and were right 7 times, but flaky is the only category that can reach `high`, so that says little about the other four.

### The diff when the trace names no changed file

The run above showed where the LLM had little to go on. The payload carries only the diff of files that the stack trace names, and the trace of an assertion in a test names the test file, not the code that changed. 13 of the 38 cases with a diff had no file in common with their trace, and the real failure from [adoption.md](adoption.md) was one of them. Since the change for issue 75, a group whose trace names no changed file gets the start of the whole diff instead, at most 150 lines, behind a first line that says so. A diff that matches by path is sent as before.

I ran the `eval` workflow twice on the same 39 cases and 49 groups, once before the change and once after. The cases now include `real-preview-item-title` with its diff. Both files are in `evals/results/`: `2026-10-06-before-diff-fallback.json` and `2026-10-06-after-diff-fallback.json`. The heuristics do not read the diff and score 28 of 49 in both runs. Sonnet:

| Category | Groups | Sonnet before | Sonnet after |
|---|---|---|---|
| product_bug | 17 | 7 | 13 |
| test_bug | 9 | 5 | 9 |
| environment | 9 | 7 | 6 |
| flaky | 7 | 4 | 4 |
| unknown | 7 | 4 | 4 |
| all | 49 | 27 (55%) | 36 (73%) |

By source, the injected groups went from 23 to 31 of 40, the mutation groups stayed at 4 of 8, and the `real` group went from `unknown` with low confidence to `product_bug` with high confidence. Answers of `unknown` fell from 18 to 8. Its `high` answers were right 12 times out of 15 before and 19 out of 22 after.

Ten groups turned from wrong to right and one turned from right to wrong. The gains are `product-bug-fee-rate-typo` (3 groups), `product-bug-fee-rounding`, `product-bug-funds-check-ignores-fee`, `test-bug-fixture-leak` (4 groups) and the real group. The loss is `environment-ledger-on-every-transfer`: the diff adds the ledger call to every transfer, and the model now blames that change instead of the unreachable ledger.

Read these numbers with care. Each configuration ran once. That one group went the other way in the first run on this page, was right in the run before the change and wrong in the run after, so it flips between runs on its own. Seven of the ten gained groups belong to two cases, and no mutation group changed its result, although seven mutation cases had no path match. The dataset has 39 cases, so I would trust the direction more than the size. The change cost about 4,300 input tokens over 49 groups: $0.358 before and $0.370 after, so $0.0076 per group. `claude-haiku-4-5` again made no successful call in either run.
