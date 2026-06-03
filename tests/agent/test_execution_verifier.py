"""Tests for the rule-based execution verifier."""

from __future__ import annotations

from nanobot.agent.verifier import RuleExecutionVerifier


def test_verifier_detects_repeated_failure():
    verifier = RuleExecutionVerifier()

    decision = verifier.verify(
        {"goal_id": "g1", "current_step": "Download PDF"},
        tool_events=[
            {"name": "read_file", "status": "error", "detail": "403"},
            {"name": "read_file", "status": "error", "detail": "403"},
        ],
    )

    assert decision.status == "repeated_failure"
    assert decision.repeated_failure is True
    assert decision.need_replan is True
    assert decision.should_reflect is True


def test_verifier_detects_stalled_step():
    verifier = RuleExecutionVerifier(stall_turn_limit=2)

    decision = verifier.verify(
        {
            "goal_id": "g1",
            "current_step": "Download PDF",
            "_stalled_turn_count": 2,
        }
    )

    assert decision.status == "stuck"
    assert decision.need_replan is True
    assert decision.should_reflect is True


def test_verifier_marks_current_step_completed():
    verifier = RuleExecutionVerifier()

    decision = verifier.verify(
        {
            "goal_id": "g1",
            "current_step": "Inspect repo",
            "plan_steps": ["Inspect repo", "Implement fix"],
            "completed_steps": ["Inspect repo"],
        }
    )

    assert decision.status == "step_completed"
    assert decision.progress_made is True
    assert decision.step_completed is True
    assert decision.suggested_next_step == "Implement fix"


def test_verifier_marks_goal_ready_to_complete():
    verifier = RuleExecutionVerifier()

    decision = verifier.verify(
        {
            "goal_id": "g1",
            "plan_steps": ["Inspect repo", "Implement fix"],
            "completed_steps": ["Inspect repo", "Implement fix"],
        }
    )

    assert decision.status == "ready_to_complete"
    assert decision.need_replan is False
    assert decision.suggested_next_step == "Call complete_goal with a final recap."


def test_verifier_detects_repeated_failure_from_trajectory_history():
    verifier = RuleExecutionVerifier()

    decision = verifier.verify(
        {"goal_id": "g1", "current_step": "Download PDF"},
        tool_events=[
            {
                "name": "read_file",
                "status": "error",
                "arguments": {"path": "missing.txt"},
                "detail": "403",
            },
        ],
        trajectory_events=[
            {
                "event_type": "tool_call_failed",
                "payload": {
                    "tool_name": "read_file",
                    "tool_args_preview": {"path": "missing.txt"},
                },
            },
        ],
    )

    assert decision.status == "repeated_failure"
    assert decision.repeated_failure is True
    assert decision.need_replan is True
    assert decision.should_reflect is True


def test_verifier_resets_repeated_failure_after_replan_update():
    verifier = RuleExecutionVerifier()

    decision = verifier.verify(
        {"goal_id": "g1", "current_step": "Try fallback source"},
        tool_events=[],
        trajectory_events=[
            {
                "event_type": "tool_call_failed",
                "payload": {
                    "tool_name": "read_file",
                    "tool_args_preview": {"path": "missing.txt"},
                },
            },
            {
                "event_type": "tool_call_failed",
                "payload": {
                    "tool_name": "read_file",
                    "tool_args_preview": {"path": "missing.txt"},
                },
            },
            {
                "event_type": "goal_state_updated",
                "payload": {"event_type": "replan"},
            },
        ],
    )

    assert decision.status == "on_track"
    assert decision.repeated_failure is False
