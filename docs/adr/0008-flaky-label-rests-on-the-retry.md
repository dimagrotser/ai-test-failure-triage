# A flaky label rests on the retry, not on where the cause sits

ADR 0001 says a group is flaky only with evidence of nondeterminism. ADR 0004 says a Lab label is trusted when the build can show the cause, and for a product bug that means reverting the patch turns the run green. For the first six flaky cases the two point in different directions. The patch that makes the test unstable is also the diff, so reverting it turns the run green, which is the counterfactual of a product bug. Sonnet read three of them that way and quoted the diff as its reason (#84).

I keep the label `flaky`. For a flaky case the check is the retry: the test fails on the first Attempt, passes on the second, and history has both runs on one tree. Flaky stays defined by evidence in the report, not by where the cause is, and ADR 0001 only says what is needed for the label, not that a model must give it whenever the evidence is there.

What changes is that the Lab says where the cause sits. `scenario.yaml` has `diff_shows_cause`, true by default. A flaky scenario can have a `base.patch`, the instability that is already on main, and then `diff.patch` is a pull request that changes something else and the field is false. The build checks that the pull request neither cures nor changes the instability and that none of its files appears in the failing trace. The eval reports flaky for both kinds.

The price is that the six older cases measure a model that can see the mechanism. A model that calls those a bug of the change is giving a defensible reading, so I do not count the disagreement as an error to remove, I report it apart. The new cases measure the more common situation, a test that was already unstable and a diff about something else.
