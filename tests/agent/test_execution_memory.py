"""Tests for task-focused execution memory retrieval and persistence."""

from __future__ import annotations

from nanobot.agent.execution_memory import ExecutionMemoryStore


def _goal_state() -> dict[str, object]:
    return {
        "goal_id": "goal-1",
        "objective": "Download and summarize the paper.",
        "current_step": "Download the PDF",
        "progress_summary": "Found the paper and now need the PDF file.",
    }


def test_append_episode_persists_required_fields(tmp_path):
    store = ExecutionMemoryStore(tmp_path)

    record = store.append_episode(
        _goal_state(),
        event_type="goal_started",
        summary="Registered the goal.",
        step="Find the paper",
        status="info",
        verified_facts=["The paper exists on arXiv."],
    )

    assert record is not None
    assert record["goal_id"] == "goal-1"
    assert record["event_type"] == "goal_started"
    assert record["summary"] == "Registered the goal."
    assert record["step"] == "Find the paper"
    assert record["status"] == "info"
    assert record["verified_facts"] == ["The paper exists on arXiv."]

    entries = store.read_entries("goal-1")
    assert len(entries) == 1
    assert entries[0]["objective"] == "Download and summarize the paper."
    assert "timestamp" in entries[0]


def test_retrieve_prefers_relevant_failures_for_current_step(tmp_path):
    store = ExecutionMemoryStore(tmp_path)
    goal = _goal_state()

    store.append_episode(
        goal,
        event_type="progress_update",
        summary="Located the paper landing page.",
        step="Find the paper",
        status="info",
    )
    store.append_episode(
        goal,
        event_type="failure",
        summary="The mirror PDF URL returned 403.",
        step="Download the PDF",
        status="failure",
        verified_facts=["The mirror host rejects direct download."],
    )
    store.append_episode(
        goal,
        event_type="progress_update",
        summary="Collected author names for the summary.",
        step="Summarize method",
        status="info",
    )

    retrieved = store.retrieve(goal, "retry the pdf download", limit=2)

    assert len(retrieved) == 2
    assert retrieved[0]["status"] == "failure"
    assert retrieved[0]["summary"] == "The mirror PDF URL returned 403."

    lines = store.build_runtime_recall_lines(goal, "retry the pdf download", limit=2)
    text = "\n".join(lines)
    assert "Execution Memory Recall:" in text
    assert "mirror PDF URL returned 403" in text
    assert "facts=The mirror host rejects direct download." in text
