# Flaky requires evidence of nondeterminism

The five categories stay a flat enum, because accuracy and the confusion matrix are reported per category. Flaky overlaps with test_bug and environment by cause, so I define it by evidence instead: a group is flaky only if a test passed on retry or both passed and failed on the same commit in history. A network timeout that fails every time is environment, not flaky. This keeps lab labels unambiguous, at the cost of calling some truly intermittent failures unknown or environment when no retry or history exists.
