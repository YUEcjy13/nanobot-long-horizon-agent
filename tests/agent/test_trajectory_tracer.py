"""Tests for structured trajectory tracing."""

from __future__ import annotations

from nanobot.agent.trajectory import TrajectoryTracer


def _goal(goal_id: str = "goal-trajectory") -> dict[str, object]:
    return {
        "goal_id": goal_id,
        "status": "active",
        "current_step": "Download PDF",
    }


def test_append_event_skips_missing_goal_id(tmp_path):
    tracer = TrajectoryTracer(tmp_path)

    row = tracer.append_event({}, event_type="goal_started")

    assert row is None
    assert list((tmp_path / "traces").glob("trajectory_*.jsonl")) == []


def test_read_events_returns_only_goal_rows(tmp_path):
    tracer = TrajectoryTracer(tmp_path)
    tracer.append_event(_goal("goal-a"), event_type="goal_started", payload={"x": 1})
    tracer.append_event(_goal("goal-b"), event_type="goal_started", payload={"x": 2})

    rows = tracer.read_events("goal-a")

    assert len(rows) == 1
    assert rows[0]["goal_id"] == "goal-a"
    assert rows[0]["payload"]["x"] == 1


def test_summarize_goal_counts_failures_reflections_and_completion(tmp_path):
    tracer = TrajectoryTracer(tmp_path)
    goal = _goal()

    tracer.append_event(
        goal,
        event_type="tool_call_failed",
        payload={"tool_name": "read_file", "tool_args_preview": "{'path': 'missing.txt'}"},
    )
    tracer.append_event(
        goal,
        event_type="tool_call_failed",
        payload={"tool_name": "read_file", "tool_args_preview": "{'path': 'missing.txt'}"},
    )
    tracer.append_event(goal, event_type="reflection_created", payload={"root_cause": "403"})
    tracer.append_event(goal, event_type="replan_requested", payload={"status": "repeated_failure"})
    tracer.append_event(goal, event_type="goal_completed", payload={"recap": "done"})

    summary = tracer.summarize_goal("goal-trajectory")

    assert summary["event_count"] == 5
    assert summary["failed_tool_calls"] == 2
    assert summary["repeated_failed_tool_calls"] == 1
    assert summary["reflection_events"] == 1
    assert summary["replan_events"] == 1
    assert summary["completed"] is True
