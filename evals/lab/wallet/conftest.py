import os
import random

from _pytest.runner import runtestprotocol

# The lab asks for retries with WALLET_RETRIES. pytest-rerunfailures leaves the failed
# attempt out of junit.xml, so retries are done here and every attempt is reported.
# Each attempt gets a fixed seed, so "random" failures are the same on every build.
RETRIES = int(os.environ.get("WALLET_RETRIES", "0"))


def pytest_runtest_protocol(item, nextitem):  # type: ignore[no-untyped-def]
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    recovered = 0
    for attempt in range(1, RETRIES + 2):
        random.seed(f"attempt-{attempt}")
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
        failed = sum(report.failed for report in reports)
        if not failed or attempt > RETRIES:
            break
        recovered += failed
        for report in reports:
            item.ihook.pytest_runtest_logreport(report=report)
    if not failed:
        # The earlier failures were retried away, so they must not fail the run.
        item.session.testsfailed -= recovered
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
