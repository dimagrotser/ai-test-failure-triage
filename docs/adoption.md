# Adoption in other repositories

On 2026-10-06 I added the released action (`v1.0.0`) to my other repositories that have tests and ran it on real pull requests. Each repository has a different report format. This note says what happened, including where the tool was no help.

## What I set up

| Repository | Tests | Input | Where it runs |
|---|---|---|---|
| [ai-test-design-agents](https://github.com/dimagrotser/ai-test-design-agents/pull/5) | pytest | `junit` | in the `tests` job, right after pytest |
| [aws-payments-floci](https://github.com/dimagrotser/aws-payments-floci/pull/1) | pytest, Playwright | `allure`, three result directories | in a new `triage` job that downloads the `allure-*` artifacts |
| [pr-preview-environments](https://github.com/dimagrotser/pr-preview-environments/pull/3) | Playwright | `playwright` | in the `preview` job, after the e2e step that is allowed to fail |

Each needed a few lines of workflow and an `ANTHROPIC_API_KEY` secret. The permissions block grew by `actions: read` in one repository, since the others already had the rest. The one with pinned action SHAs got the SHA of `v1.0.0` too. The action did not fail to install or run in any of them, and no input format needed a workaround.

## Runs without failures

All the pull requests passed, so the action had nothing to report. It ran in about 4 seconds, made no LLM calls, cost $0, and logged `github: comment skipped, no failures`. In `aws-payments-floci` it read the Allure results of every suite without trouble. This only shows the setup works. It says nothing about the quality of a report.

## A real failure

`pr-preview-environments` has a pull request that fails on purpose ([#2](https://github.com/dimagrotser/pr-preview-environments/pull/2)). The frontend reads `item.title` while the API returns `item.name`, so the list renders empty. After the action was on `main` I merged `main` into that branch and the e2e run failed as intended.

The action posted one comment: one group, `unknown`, low confidence. It quoted the right evidence (`Expected: "Namespace per pull request"`, `Received: ""`) and said the cause could be the app or the test. It cost $0.0065 (1,968 input tokens, 258 output tokens).

That answer is honest, but it is not the right one. The true category is `product_bug`, and the diff changes exactly one line in `apps/web/public/app.js`. The report says "there is no diff to help", which is wrong, because the pull request has a diff. The reason is how the diff is filtered. `select_hunks` keeps only files whose path appears in the stack trace, and a Playwright trace names the test file, never the application file it exercises. For an end-to-end failure the changed code is therefore always dropped. I did not fix this here. It is the most useful thing this adoption found.

I imported the failure as `real-preview-item-title`, labeled `product_bug` by hand. The heuristics alone call it `test_bug`, so the first `real` group scores 0 of 1:

```
Source        Groups  Correct  Accuracy
injected          40       23       58%
mutation           8        5       62%
real               1        0        0%
```

One group says almost nothing about accuracy. I did not rerun the LLM eval, because that costs money for a single case. The Sonnet answer in CI above is the only LLM data point for it.

## Other things I noticed

- `aws-payments-floci` runs its `triage` job after `tests`, which takes about 6 minutes 40 seconds. The action itself needs 4 seconds, so the pull request waits on the slow job for the comment.
- A passing run leaves no trace on the pull request. That is the intended behavior, but when I looked for proof that the action ran, I had to read the job log.
- Playwright does not put the JUnit file anywhere by default. For the import I added a JUnit reporter and an artifact upload to the demo branch.
- No history exists yet. The runs were on pull requests, and history comes from runs on `main`, so the report says `History: none`.

## Limitations

The sample is small: a few repositories of mine, one real failure, one run of it. The failure was staged, so it tells me less than an accidental one would. Pull requests that fail by accident will show up over the next weeks, and I will import them as they come.
