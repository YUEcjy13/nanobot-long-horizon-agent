"""Tests for reflection building and recall persistence."""

from __future__ import annotations

from nanobot.agent.execution_memory import ExecutionMemoryStore
from nanobot.agent.reflection import ReflectionBuilder
from nanobot.agent.verifier import VerifierDecision


def _goal() -> dict[str, object]:
    return {
        "goal_id": "goal-reflection",
        "objective": "Download the paper PDF.",
        "current_step": "Download PDF",
    }


def test_reflection_builder_returns_none_without_failure_signal():
    builder = ReflectionBuilder()
    decision = VerifierDecision(
        status="on_track",
        confidence=0.5,
        progress_made=False,
        step_completed=False,
        need_replan=False,
        repeated_failure=False,
        should_reflect=False,
        root_cause="",
        evidence=[],
        suggested_next_step="Continue.",
    )

    reflection = builder.build(_goal(), decision, failure_notes=[])

    assert reflection is None


def test_reflection_builder_generates_rule_based_summary():
    builder = ReflectionBuilder()
    decision = VerifierDecision(
        status="repeated_failure",
        confidence=0.92,
        progress_made=False,
        step_completed=False,
        need_replan=True,
        repeated_failure=True,
        should_reflect=True,
        root_cause="The same mirror path failed repeatedly.",
        evidence=["Repeated tool failures: read_file"],
        suggested_next_step="Try an alternate source.",
    )

    reflection = builder.build(
        _goal(),
        decision,
        failure_notes=["read_file: mirror returned 403"],
    )

    assert reflection is not None
    assert reflection.root_cause == "The same mirror path failed repeatedly."
    assert "Do not retry the same failing action" in reflection.avoid_next_time
    assert reflection.suggested_strategy == "Try an alternate source."


def test_reflection_summary_persists_into_execution_memory(tmp_path):
    store = ExecutionMemoryStore(tmp_path)
    builder = ReflectionBuilder()
    decision = VerifierDecision(
        status="stuck",
        confidence=0.86,
        progress_made=False,
        step_completed=False,
        need_replan=True,
        repeated_failure=False,
        should_reflect=True,
        root_cause="The current step is stuck with no progress.",
        evidence=["No new progress signal was observed across repeated turns."],
        suggested_next_step="Switch to a new retrieval path.",
    )

    reflection = builder.build(
        _goal(),
        decision,
        failure_notes=["browser_download: repeated timeout"],
    )
    assert reflection is not None

    store.append_episode(
        _goal(),
        event_type="reflection",
        summary=reflection.to_summary(),
        step=reflection.failed_step,
        status=decision.status,
        details=reflection.failed_action,
        verified_facts=[
            f"Root cause: {reflection.root_cause}",
            f"Suggested strategy: {reflection.suggested_strategy}",
        ],
    )

    lines = store.build_runtime_recall_lines(_goal(), "download pdf again", limit=2)

    assert any("[reflection]" in line for line in lines)
    assert any("Root cause:" in line for line in lines)
