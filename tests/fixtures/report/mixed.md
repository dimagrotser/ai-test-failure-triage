**3 failed tests in 3 groups**, 0 passed on retry, 6 tests total.

History: none. No earlier runs on main were read, so nothing here says a test was stable before.

### 1. unknown, low confidence

No clear cause in the failure output

Tests (1): `tests.test_cart::test_total_with_discount`

Evidence:

```text
tests/test_cart.py:21: AssertionError
assert 90 == 95
```

Next step: Read the failure output, there is not enough evidence to classify it

Classified by heuristics, not sent to LLM.

### 2. environment, medium confidence

The failure points at the CI environment

Tests (1): `tests/test_api.py::test_fetch_profile`

Evidence:

```text
requests.exceptions.ConnectionError: HTTPConnectionPool(host='staging.internal', port=8080): Max retries exceeded
tests/test_api.py:44: ConnectionError
```

Next step: Check the service or setting in the quote, then rerun

Classified by heuristics, not sent to LLM.

### 3. environment, medium confidence

The failure points at the CI environment

Tests (1): `tests.test_db::test_migrations_apply`

Evidence:

```text
failed on setup with "sqlalchemy.exc.OperationalError: connection refused"
tests/conftest.py:12: in db
```

Next step: Check the service or setting in the quote, then rerun

Classified by heuristics, not sent to LLM.
