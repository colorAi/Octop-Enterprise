"""tests/unit/test_metrics.py"""

from __future__ import annotations

from octop.infra.metrics import METRICS, Metrics


def test_metrics_inc_and_snapshot() -> None:
    m = Metrics()
    m.inc("messages_total")
    m.inc("messages_total", 4)
    m.inc("stream_errors_total")
    snap = m.snapshot()
    assert snap["messages_total"] == 5
    assert snap["stream_errors_total"] == 1
    assert snap["cron_runs_total"] == 0


def test_metrics_set() -> None:
    m = Metrics()
    m.set("agent_active", 3)
    assert m.snapshot()["agent_active"] == 3


def test_singleton_exists() -> None:
    snap = METRICS.snapshot()
    assert "messages_total" in snap
    assert "agent_active" in snap
