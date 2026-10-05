# failtriage

After a CI run, failtriage reads the JUnit report, removes secrets and personal data, groups the failures by root cause and classifies each group as `product_bug`, `test_bug`, `flaky`, `environment` or `unknown`. It posts one report on the pull request. Fixed rules go first and an LLM second, and every verdict quotes its evidence.

## Usage

Run it after your tests, in a job that has the report on disk:

```yaml
- uses: dimagrotser/ai-test-failure-triage@main
  if: failure()
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

| Input | Default | |
|---|---|---|
| `junit` | required | JUnit XML report |
| `github-token` | `github.token` | token for the GitHub API |
| `anthropic-api-key` | none | enables LLM classification |
| `model` | `claude-sonnet-5-5` | model that classifies the groups |
| `comment` | `true` | `false` keeps the report in the job summary only |
| `comment-key` | `default` | name of the comment, to keep several per pull request |

`report` is the path of the JSON report and `groups` is the number of failure groups. The report is always written to the job summary too. A report that does not fit into a comment is cut there and the whole text stays in the summary.

## CLI

```
uv sync
uv run failtriage analyze --junit tests/fixtures/junit/mixed.xml --markdown
```

## Limitations

Only JUnit XML is read so far. History on main is not uploaded yet, so reports say `History: none` and nothing can be called flaky from history alone, only from a pass on retry.
