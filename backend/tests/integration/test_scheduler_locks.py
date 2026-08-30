"""Only one process may run each scheduled job.

`process_role` excludes the `worker` role from starting a scheduler, but the default
`api` role still starts one — so a deployment with three API replicas has three
schedulers. `run_recovery_cycle` already took its own advisory lock, so the daily
chase was safe. The other jobs were not, and the retry sweep is the one that matters:
two concurrent sweeps can lease and resend the same failed reminder, and the customer
receives the same demand twice.
"""

import pytest

from app.scheduler.jobs import _only_one_runner


def test_the_first_runner_gets_the_lock(session):
    with _only_one_runner("retry_operations") as mine:
        assert mine is True


def test_a_second_runner_is_turned_away_while_the_first_holds_it(session):
    """The replica case: two processes, same job, same moment."""
    with _only_one_runner("retry_operations") as first:
        assert first is True
        with _only_one_runner("retry_operations") as second:
            assert second is False, "a second scheduler ran the same job concurrently"


def test_the_lock_is_released_afterwards(session):
    """A lock that leaked would silently stop the job forever after one run."""
    with _only_one_runner("retry_operations") as mine:
        assert mine is True
    with _only_one_runner("retry_operations") as again:
        assert again is True


def test_the_lock_is_released_even_when_the_job_raises(session):
    with pytest.raises(RuntimeError), _only_one_runner("retry_operations") as mine:
        assert mine is True
        raise RuntimeError("job blew up")
    with _only_one_runner("retry_operations") as again:
        assert again is True, "a crashed job left its lock held"


def test_different_jobs_do_not_block_each_other(session):
    """Distinct keys, so the hourly sync must not be starved by the retry sweep."""
    with _only_one_runner("retry_operations") as first:
        assert first is True
        with _only_one_runner("payment_link_sync") as second:
            assert second is True
