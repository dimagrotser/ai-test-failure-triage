**41 failed tests in 1 group**, 0 passed on retry, 59 tests total.

History: none. No earlier runs on main were read, so nothing here says a test was stable before.

### 1. product_bug, low confidence

The failing frame is in source code

Tests (41): `tests.test_balance::test_deposit_increases_the_balance`, `tests.test_balance::test_deposit_keeps_every_cent`, `tests.test_balance::test_deposit_keeps_every_cent`, `tests.test_balance::test_deposit_keeps_every_cent`, `tests.test_balance::test_deposit_keeps_every_cent`, and 36 more

Evidence:

```text
wallet/accounts.py:16: TypeError
```

Next step: Check the source code at the failing frame

Classified by heuristics, not sent to LLM.
