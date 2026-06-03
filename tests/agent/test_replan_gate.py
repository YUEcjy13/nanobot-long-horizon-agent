"""Tests for verifier-gated replanning requests."""

from __future__ import annotations

from nanobot.agent.reflection import Reflection
from nanobot.agent.replan_gate import ReplanGate
from nanobot.agent.verifier import VerifierDecision


def _goal(replan_count: int = 0) -> dict[str, object]:
    return {
        "goal_id": "goal-gate",
        "current_step": "Download PDF",
        "plan_steps": ["Find paper", "Download PDF", "Summarize method"],
        "replan_count": replan_count,
    }


def _decision() -> VerifierDecision:
    return VerifierDecision(
        status="repeated_failure",
        confidence=0.92,
        progress_made=False,
        step_completed=False,
        need_replan=True,
        repeated_failure=True,
        should_reflect=True,
        root_cause="The same download path failed repeatedly.",
        evidence=["Repeated tool failures: read_file"],
        suggested_next_step="Switch to a new source.",
    )


def test_replan_gate_includes_verifier_and_reflection_details():
    gate = ReplanGate(max_replans_per_goal=3)
    reflection = Reflection(
        goal_id="goal-gate",
        failed_step="Download PDF",
        failed_action="read_file: mirror returned 403",
        root_cause="The same download path failed repeatedly.",
        avoid_next_time="Do not retry the same failing mirror.",
        suggested_strategy="Switch to a new source.",
        confidence=0.9,
    )

    injection = gate.build_injection(
        _goal(),
        _decision(),
        reflection,
        failure_notes=["read_file: mirror returned 403"],
    )
    text = injection["content"]

    assert "[System-generated execution supervisor request]" in text
    assert "Status: repeated_failure" in text
    assert "Avoid next time: Do not retry the same failing mirror." in text
    assert "mark_replanned=true" in text


def test_replan_gate_stops_after_budget_exhausted():
    gate = ReplanGate(max_replans_per_goal=2)

    injection = gate.build_injection(
        _goal(replan_count=2),
        _decision(),
        None,
        failure_notes=["read_file: mirror returned 403"],
    )
    text = injection["content"]

    assert "already been replanned 2 times" in text
    assert "Do not continue blind auto-replanning." in text
    assert "ask for clarification" in text
