**2 failure groups**: 1 failed test, 1 passed on retry, 3 tests total.

History: none. No earlier runs on main were available, so nothing here says a test was stable before.

### 1. flaky, high confidence

Failed, then passed on retry

Tests (1): `com.acme.shop.OrderServiceTest::shouldReserveStock`

Evidence:

```text
com.acme.shop.OrderServiceTest::shouldReserveStock: failed, then passed on attempt 3
com.acme.shop.OrderServiceTest.shouldReserveStock
```

Next step: Find the source of nondeterminism in the test or the code it calls

Classified by heuristics, not sent to LLM.

### 2. environment, medium confidence

The failure points at the CI environment

Tests (1): `com.acme.shop.OrderServiceTest::shouldChargeCard`

Evidence:

```text
Connection refused
com.acme.shop.PaymentClient.charge
```

Next step: Check the service or setting in the quote, then rerun

Classified by heuristics, not sent to LLM.
