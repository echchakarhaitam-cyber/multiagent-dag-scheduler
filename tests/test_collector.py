"""Unit tests for the result collector."""

from dagsched import ResultCollector


def test_first_delivery_accepted():
    collector = ResultCollector()
    assert collector.idempotent
    assert collector.deliver("A", 1) is True
    assert collector.has_result("A")
    assert collector.accepted_attempt("A") == 1
    assert collector.accepted == 1
    assert collector.duplicates_ignored == 0


def test_duplicate_ignored_and_counted():
    collector = ResultCollector()
    collector.deliver("A", 1)
    assert collector.deliver("A", 1) is False
    assert collector.deliver("A", 2) is False
    assert collector.accepted == 1
    assert collector.duplicates_ignored == 2
    assert collector.accepted_attempt("A") == 1


def test_different_tasks_are_independent():
    collector = ResultCollector()
    assert collector.deliver("A", 1) and collector.deliver("B", 3)
    assert collector.accepted == 2
    assert collector.accepted_attempt("B") == 3


def test_unknown_task_has_no_result():
    collector = ResultCollector()
    assert not collector.has_result("A")
    assert collector.accepted_attempt("A") is None


def test_naive_mode_accepts_every_delivery():
    collector = ResultCollector(idempotent=False)
    assert all(collector.deliver("A", 1) for _ in range(3))
    assert collector.accepted == 3
    assert collector.duplicates_ignored == 0
    assert collector.accepted_attempt("A") == 1
