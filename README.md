# failtriage

After a CI run, failtriage reads the JUnit or Playwright JSON report, removes secrets and personal data, groups the failures by root cause and classifies each group as `product_bug`, `test_bug`, `flaky`, `environment` or `unknown`. It posts one report on the pull request. Fixed rules go first and an LLM second, and every verdict quotes its evidence.

## Usage

Run it after your tests, in a job that has the report on disk:

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

`report` is the path of the JSON report and `groups` is the number of failure groups. The report is always written to the job summary too. A report that does not fit into a comment is cut there and the whole text stays in the summary.

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

## Limitations

Only JUnit XML, Playwright JSON and Allure result files are read. Allure steps and attachments are kept in the parsed data but do not reach the LLM yet. A repository needs a few runs on main before history signals appear. The eval dataset has 48 failure groups and no real failures yet, so its accuracy numbers are a rough guide and say nothing about your own tests.
