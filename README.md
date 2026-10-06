# failtriage

After a CI run, failtriage reads the JUnit or Playwright JSON report, removes secrets and personal data, groups the failures by root cause and classifies each group as `product_bug`, `test_bug`, `flaky`, `environment` or `unknown`. It posts one report on the pull request. Fixed rules go first and an LLM second, and every verdict quotes its evidence.

A red CI run with forty failures rarely has forty causes. Usually it is two or three: a service that was down, one wrong constant, a test that depends on order. Reading the logs to find that out costs more than the fix. failtriage does the grouping and the first guess, and it shows the lines it based the guess on, so you can check it in seconds.

## Example report

This is what `failtriage analyze --junit tests/fixtures/junit/mixed.xml --markdown` prints, with no API key set, so the verdicts come from the rules alone:

> **3 failure groups**: 3 failed tests, 0 passed on retry, 6 tests total.
>
> History: none. No earlier runs on main were available, so nothing here says a test was stable before.
>
> ### 2. environment, medium confidence
>
> The failure points at the CI environment
>
> Tests (1): `tests/test_api.py::test_fetch_profile`
>
> ```text
> requests.exceptions.ConnectionError: HTTPConnectionPool(host='staging.internal', port=8080): Max retries exceeded
> tests/test_api.py:44: ConnectionError
> ```
>
> Next step: Check the service or setting in the quote, then rerun

<!-- screenshot of the comment on a real pull request goes here -->

## Quickstart

Add this step after your tests, in a job that has the report on disk:

```yaml
- uses: dimagrotser/ai-test-failure-triage@main
  if: ${{ !cancelled() }}
  with:
    junit: reports/junit.xml
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Without `anthropic-api-key` the groups are classified by heuristics only. There is no release tag yet, so the example follows `main`.

## Permissions

The job needs these and nothing more:

```yaml
permissions:
  pull-requests: write  # create and update the report comment
  actions: read         # read history artifacts of earlier runs on main
  contents: read        # check out the repository and read the pull request files
```

The action never runs on `pull_request_target` and stops with an error if you trigger it from there. That event hands a write token to code from a fork.

## Fork pull requests

GitHub gives fork pull requests a read-only token and no secrets, so the action cannot comment there. It detects forks and Dependabot runs, skips the comment and the LLM, and classifies with heuristics only. The full report goes to the job summary of the run. If your workflow drops `pull-requests: write` for another reason, set `comment: false` to get the same behavior.

## Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `junit` | none | JUnit XML reports, one path per line |
| `playwright` | none | Playwright JSON reports, one path per line |
| `allure` | none | Allure results directories, one path per line |
| `github-token` | `github.token` | token for the GitHub API |
| `anthropic-api-key` | none | enables LLM classification |
| `model` | `claude-sonnet-5-5` | model that classifies the groups |
| `comment` | `true` | `false` keeps the report in the job summary only |
| `comment-key` | `default` | name of the comment, to keep several per pull request |

Set one of `junit`, `playwright` and `allure`. Several files of one format are fine, for example one report per shard:

```yaml
with:
  playwright: |
    reports/shard-1.json
    reports/shard-2.json
```

The Playwright reporter has to be `json`, for example `npx playwright test --reporter=json > reports/pw.json`.

For Allure, point `allure` at the results directory, usually `allure-results`. The action reads the `*-result.json` files in it, not the HTML report. Attachments are listed by path and never opened. Retries of one test arrive as separate files that share a `historyId`, and they become the attempts of one test.

| Output | Meaning |
|---|---|
| `report` | path of the JSON report |
| `groups` | number of failure groups |

The report is always written to the job summary too. A report that does not fit into a comment is cut there and the whole text stays in the summary.

## History

On runs of `main` the action uploads an artifact called `failtriage-history`. It holds one file, `history.json`: a JSON array with one entry per test in the report, passed tests included.

```json
[{"test_id": "tests/test_fees.py::test_fee", "status": "passed", "attempts": 1, "sha": "3f2c...", "run_id": 9003}]
```

Each entry has exactly these five fields and no text from the report, so there is nothing to redact. `status` is one of `passed`, `failed`, `error`, `skipped` or `passed_on_retry`. `failtriage schema --history` prints the schema, and `history.schema.json` is the committed copy.

Pull request runs never upload it. They read the artifacts of the last 10 runs on main instead, which is why they need `actions: read`. The upload counts as a run of main when `github.ref` is `refs/heads/main` and the event is not `pull_request`, so every other event on main qualifies, a push as much as a scheduled run.

Artifacts expire with your repository's retention setting (90 days by default). Until the first run on main has uploaded one, or when a fork pull request cannot read it, the report says `History: none` and nothing is called flaky from history. If a workflow runs the action in two jobs on main, the second upload fails because the artifact name is taken.

## CLI

```
uv sync
uv run failtriage analyze --junit tests/fixtures/junit/mixed.xml --markdown
uv run failtriage analyze --playwright tests/fixtures/playwright/mixed.json --markdown
uv run failtriage analyze --allure tests/fixtures/allure/mixed --markdown
```

`--junit`, `--playwright` and `--allure` exclude each other and can be repeated for several files or directories of one format.

`uv run failtriage eval evals/` scores the classifier against the labeled failures in `evals/cases/`. With `ANTHROPIC_API_KEY` set it compares the heuristics alone with the heuristics plus the LLM for Sonnet and Haiku, and reports tokens and cost. [docs/lab.md](docs/lab.md) has the details and the current numbers. The `eval` workflow runs it on demand and is never part of CI.

## Accuracy

I scored the classifier on 38 failing runs of a small wallet app, 48 failure groups in all. Every label was verified by a counterfactual run, for example by reverting the patch that broke the app ([docs/lab.md](docs/lab.md) explains how). The numbers below come from one run of the `eval` workflow on 2026-10-06, committed as `evals/results/2026-10-06.json`.

| Category | Groups | Heuristics | Heuristics and Sonnet |
|---|---|---|---|
| product_bug | 16 | 7 | 7 |
| test_bug | 9 | 2 | 5 |
| environment | 9 | 8 | 7 |
| flaky | 7 | 7 | 6 |
| unknown | 7 | 4 | 4 |
| all | 48 | 28 (58%) | 29 (60%) |

By source, the 40 injected groups score 23 with the heuristics and 25 with Sonnet. The 8 mutation groups score 5 and 4. There are no `real` groups, so nothing here says how it does on failures I did not write myself.

Sonnet barely beats the rules. It helps on `test_bug` and loses a few groups the rules had right. `product_bug` is the weak spot in both columns, at 44%: when a test fails on an assertion, nobody can tell from the log alone whether the test or the product is wrong, so the tool says `unknown` on purpose. Of its `high` confidence answers Sonnet got 14 of 16 right.

I could not compare models. `claude-haiku-4-5` made no successful call in that run, every group fell back to the heuristics, and I have not found out why. With 48 groups, one case moves a category by several points, and I ran it once.

## Cost

The same run cost $0.35 for Sonnet: 48 calls, 108,156 input tokens and 13,295 output tokens, about $0.007 per group. A run sends at most 10 groups to the LLM, so a pull request costs up to about $0.07 and usually less, because groups with the same cause are classified once. Every run logs its tokens and cost. Without `anthropic-api-key` it costs nothing and uses the rules only.

## Design decisions

The reasoning behind the choices that are hard to see from the code is in `docs/adr/`:

- [0001](docs/adr/0001-flaky-requires-evidence.md): a group is flaky only with evidence of nondeterminism
- [0002](docs/adr/0002-history-from-own-artifact.md): history comes from an artifact the action uploads on main
- [0003](docs/adr/0003-redact-first-fail-closed.md): redact right after parsing, check again before the LLM call
- [0004](docs/adr/0004-lab-labels-by-counterfactual.md): lab labels are verified by a counterfactual run
- [0005](docs/adr/0005-httpx-for-github-api.md): plain httpx for the GitHub API
- [0006](docs/adr/0006-cluster-before-llm.md): group failures before calling the LLM
- [0007](docs/adr/0007-real-cases-labeled-by-hand.md): real cases are labeled by hand and redacted before they touch the disk

## Limitations

Only JUnit XML, Playwright JSON and Allure result files are read, and Allure steps and attachments do not reach the LLM yet.

Fork pull requests get no comment and no LLM, because GitHub gives them a read-only token and no secrets. The report goes to the job summary and the verdicts come from the rules.

History has gaps. A repository needs a few runs on main before history signals appear, artifacts expire after the retention period, and fork runs cannot read them. Until then the report says `History: none` and nothing is called flaky from history, so a truly intermittent failure with no retry can come out as `unknown`.

The accuracy numbers are a rough guide. The dataset is small and written by me, it has no real failures, and Sonnet scores 60% on it. Treat every verdict as a lead with evidence attached, not as a ruling.
