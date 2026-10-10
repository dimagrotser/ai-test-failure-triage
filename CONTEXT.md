# Failure triage

Reads test results after a CI run, groups failures by root cause, classifies each group and reports the result on the pull request.

## Language

**TestResult**:
The outcome of one test in one run, with its final status and all of its Attempts. Parsers produce one TestResult per test, never one per retry.
_Avoid_: Failure record, test case

**Attempt**:
A single execution of a test within a run. A TestResult has one or more.
_Avoid_: Retry (a retry is an Attempt after the first), run

**Test id**:
Stable key for a test across runs: `<file or classname>::<name>`, with the Playwright project included and parametrization excluded. A Playwright test reads `login.spec.ts::chromium › checkout › pays by card`.
_Avoid_: Test name

**Passed on retry**:
Final status of a TestResult that failed at least one Attempt and passed a later one. Set by the parser, not by a heuristic.
_Avoid_: Flaky (that is a classification, not a status)

## Classification

**Failure group**:
TestResults with the same normalized error signature. The unit that gets classified and reported.
_Avoid_: Cluster, bucket

**Signature**:
Exact-match key of a Failure group: exception type, normalized first message line and the top frame from project code (file and function, no line number).
_Avoid_: Fingerprint, hash

**Signal**:
A named, deterministic observation about a Failure group (for example `passed_on_retry` or `network_error`) with a quote as proof. Has no weight.
_Avoid_: Feature, flag

**Heuristic verdict**:
The Category that fixed rules derive from Signals, or none. Passed to the LLM, which may disagree with a stated reason.

**Category**:
One of `product_bug`, `test_bug`, `flaky`, `environment`, `unknown`. Each Failure group gets exactly one.
_Avoid_: Type, label (label is the ground truth in the failure lab)

**Flaky**:
A Category assigned only with evidence of nondeterminism: Passed on retry, or both pass and fail on the same commit in history. Without that evidence a group is never flaky.

**Diff shows the cause**:
For a Lab case, whether the failing mechanism is in the diff. True by default. A flaky case can set it to false when the instability is already on main and the diff is about something else (ADR 0008). The eval reports flaky for both.

**Evidence**:
A quote from logs or the diff that supports a claim in the report. A classification without evidence must be `unknown`.

**History**:
Test statuses from recent runs on main, read from the artifact that the action uploads on main runs. May be absent, and the report says so.

## Failure lab

**Lab case**:
One reproducible failure in `evals/cases/<id>/` with a ground-truth label.

**Label**:
The ground-truth Category of a Lab case, with its source (injected, mutation, real).
