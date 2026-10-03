# Lab labels are verified by a counterfactual run

A label is only trusted if `make lab` can show the cause: reverting `diff.patch` turns a product_bug green, fixing only the test turns a test_bug green, restoring the environment turns an environment case green, and a flaky case fails on the first attempt and passes on retry. Flakiness is driven by attempt number or a fixed seed, never by real randomness, so the dataset rebuilds identically. Unknown cases have no such check by construction and say why in `notes`. A scenario that fails its check is not written.
