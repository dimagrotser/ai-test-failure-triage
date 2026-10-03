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

`scenario.yaml` needs four fields. `category` is one of `product_bug`, `test_bug`, `flaky`, `environment`, `unknown`. `source` is `injected` for everything written by hand. `scenario` is one line saying what is wrong. `notes` explains why the label is right, and for `unknown` why nobody can tell. Some categories take one more field, listed below.

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
