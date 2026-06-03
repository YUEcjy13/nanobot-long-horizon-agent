"""Tests for reflective trajectory evaluation helpers."""

from __future__ import annotations

from nanobot.utils.trajectory_eval import (
    aggregate_reflective_eval,
    enrich_reflective_record,
    summarize_trajectory_events,
)


def test_summarize_trajectory_events_counts_reflection_reuse_and_replans():
    events = [
        {"event_type": "goal_started", "payload": {}},
        {
            "event_type": "tool_call_failed",
            "payload": {"tool_name": "read_file", "tool_args_preview": "{'path': 'missing.txt'}"},
        },
        {
            "event_type": "tool_call_failed",
            "payload": {"tool_name": "read_file", "tool_args_preview": "{'path': 'missing.txt'}"},
        },
        {"event_type": "verifier_decision", "payload": {"status": "repeated_failure"}},
        {"event_type": "reflection_created", "payload": {"root_cause": "403"}},
        {"event_type": "replan_requested", "payload": {"status": "repeated_failure"}},
        {"event_type": "goal_state_updated", "payload": {"updates": {"mark_replanned": True}}},
        {"event_type": "goal_completed", "payload": {}},
    ]

    summary = summarize_trajectory_events(events)

    assert summary["repeated_failed_tool_calls"] == 1
    assert summary["reflection_count"] == 1
    assert summary["reflection_reuse_count"] == 1
    assert summary["successful_replan_count"] == 1
    assert summary["trajectory_completeness"] > 0.8


def test_enrich_and_aggregate_reflective_records():
    record = {
        "task_id": "task-1",
        "variant": "reflective_supervisor",
        "completed": True,
        "tool_call_count": 4,
        "repeated_tool_calls": 1,
        "had_failure": True,
        "recovered_after_failure": True,
        "step_count": 3,
        "replan_count": 0,
        "latency_ms": 1200,
    }
    enriched = enrich_reflective_record(
        record,
        trajectory_events=[
            {"event_type": "goal_started", "payload": {}},
            {
                "event_type": "tool_call_failed",
                "payload": {"tool_name": "read_file", "tool_args_preview": "{'path': 'x'}"},
            },
            {"event_type": "reflection_created", "payload": {}},
            {"event_type": "replan_requested", "payload": {}},
            {"event_type": "goal_state_updated", "payload": {"updates": {"mark_replanned": True}}},
            {"event_type": "goal_completed", "payload": {}},
        ],
        final_goal_state={
            "plan_steps": ["A", "B", "C"],
            "completed_steps": ["A", "B", "C"],
        },
    )

    assert enriched["successful_replan"] is True
    assert enriched["step_completion_rate"] == 1.0
    assert enriched["trajectory_completeness"] > 0.5

    summary = aggregate_reflective_eval([enriched])
    metrics = summary["reflective_supervisor"]
    assert metrics["task_success_rate"] == 1.0
    assert metrics["step_completion_rate"] == 1.0
    assert metrics["successful_replan_rate"] == 1.0
