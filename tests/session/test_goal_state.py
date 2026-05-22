"""Tests for ``goal_state`` session metadata helpers."""

from __future__ import annotations

from datetime import datetime

from nanobot.session.goal_state import (
    GOAL_STATE_KEY,
    discard_legacy_goal_state_key,
    goal_state_needs_replan,
    goal_state_replan_reasons,
    goal_state_runtime_lines,
    goal_state_ws_blob,
    normalize_goal_list,
    parse_goal_state,
    runner_wall_llm_timeout_s,
    sustained_goal_active,
)
from nanobot.session.manager import SessionManager


def test_runtime_lines_empty_when_no_metadata():
    assert goal_state_runtime_lines(None) == []
    assert goal_state_runtime_lines({}) == []


def test_runtime_lines_empty_when_completed():
    meta = {
        GOAL_STATE_KEY: {"status": "completed", "objective": "was doing X"},
    }
    assert goal_state_runtime_lines(meta) == []


def test_runtime_lines_include_objective_when_active():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Ship the fix.",
            "ui_summary": "fix",
        },
    }
    lines = goal_state_runtime_lines(meta)
    assert "Goal (active):" in lines
    assert "Ship the fix." in lines
    assert any("Summary: fix" in ln for ln in lines)


def test_runtime_lines_include_structured_progress_and_replan_hint():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Ship the fix.",
            "current_step": "Update timeout handling",
            "plan_steps": ["Inspect traces", "Update timeout handling", "Run tests"],
            "completed_steps": ["Inspect traces"],
            "progress_summary": "Confirmed the bug and started patching.",
            "blocked_reason": "Retry loop still repeats old path.",
            "recent_failures": ["First retry timed out.", "Second retry timed out."],
            "verified_facts": ["Fallback routing is never reached."],
        },
    }
    lines = goal_state_runtime_lines(meta)
    text = "\n".join(lines)
    assert "Current Step: Update timeout handling" in text
    assert "Plan Progress: 1/3 steps completed" in text
    assert "Verified Facts:" in text
    assert "Replanning Hint:" in text


def test_runtime_lines_read_legacy_thread_goal_key():
    meta = {"thread_goal": {"status": "active", "objective": "Legacy key.", "ui_summary": "L"}}
    lines = goal_state_runtime_lines(meta)
    assert "Legacy key." in lines


def test_goal_state_key_takes_precedence_over_legacy():
    meta = {
        GOAL_STATE_KEY: {"status": "active", "objective": "New key wins.", "ui_summary": "n"},
        "thread_goal": {"status": "active", "objective": "Ignored.", "ui_summary": "o"},
    }
    lines = goal_state_runtime_lines(meta)
    assert "New key wins." in lines
    assert "Ignored." not in "".join(lines)


def test_discard_legacy_goal_state_key():
    meta: dict = {"thread_goal": {"x": 1}, GOAL_STATE_KEY: {"status": "active"}}
    discard_legacy_goal_state_key(meta)
    assert "thread_goal" not in meta
    assert GOAL_STATE_KEY in meta


def test_parse_goal_state_accepts_json_string():
    assert parse_goal_state('{"status":"active","objective":"x"}') == {
        "status": "active",
        "objective": "x",
    }


def test_goal_state_ws_blob_inactive_when_missing_or_completed():
    assert goal_state_ws_blob(None) == {"active": False}
    assert goal_state_ws_blob({}) == {"active": False}
    assert goal_state_ws_blob({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}) == {
        "active": False,
    }


def test_goal_state_ws_blob_active_shape():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Build feature.",
            "ui_summary": "feat",
            "goal_id": "goal-xyz",
        },
    }
    assert goal_state_ws_blob(meta) == {
        "active": True,
        "ui_summary": "feat",
        "objective": "Build feature.",
        "goal_id": "goal-xyz",
    }


def test_goal_state_ws_blob_exposes_structured_fields_when_present():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Build feature.",
            "goal_id": "goal-123",
            "current_step": "Write tests",
            "progress_summary": "Half done.",
            "plan_steps": ["Plan", "Code", "Write tests"],
            "completed_steps": ["Plan", "Code"],
            "recent_failures": ["Old test was flaky.", "Old test was flaky again."],
            "blocked_reason": "Need a cleaner assertion.",
            "verified_facts": ["The latest refactor removed the old helper."],
            "replan_count": 2,
        },
    }
    blob = goal_state_ws_blob(meta)
    assert blob["goal_id"] == "goal-123"
    assert blob["current_step"] == "Write tests"
    assert blob["progress_summary"] == "Half done."
    assert blob["plan_steps"] == ["Plan", "Code", "Write tests"]
    assert blob["total_steps"] == 3
    assert blob["completed_steps"] == ["Plan", "Code"]
    assert blob["completed_count"] == 2
    assert blob["verified_facts"] == ["The latest refactor removed the old helper."]
    assert blob["recent_failures"] == ["Old test was flaky.", "Old test was flaky again."]
    assert blob["replan_count"] == 2
    assert blob["needs_replan"] is True


def test_sustained_goal_active_false_when_missing_or_completed():
    assert sustained_goal_active(None) is False
    assert sustained_goal_active({}) is False
    assert sustained_goal_active({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}) is False


def test_sustained_goal_active_true_when_active():
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "Run long task."}}
    assert sustained_goal_active(meta) is True


def test_sustained_goal_active_respects_legacy_thread_goal_key():
    meta = {"thread_goal": {"status": "active", "objective": "Legacy."}}
    assert sustained_goal_active(meta) is True


def test_goal_state_needs_replan_and_normalize_helpers():
    goal = {
        "recent_failures": ["A", "A", "B"],
        "blocked_reason": "Need a new plan.",
    }
    assert normalize_goal_list(goal["recent_failures"], max_items=5) == ["A", "B"]
    assert goal_state_needs_replan(goal) is True
    assert goal_state_replan_reasons(goal)


def test_goal_state_replan_reasons_detect_stalled_current_step():
    goal = {
        "current_step": "Retry fallback",
        "started_at": "2026-05-22T10:00:00",
        "last_updated_at": "2026-05-22T10:00:00",
    }
    reasons = goal_state_replan_reasons(
        goal,
        now=datetime.fromisoformat("2026-05-22T10:20:00"),
        stale_after_s=300,
    )
    assert any("stalled" in reason.lower() for reason in reasons)


def test_runner_wall_llm_timeout_uses_metadata_override(tmp_path):
    sm = SessionManager(tmp_path)
    assert (
        runner_wall_llm_timeout_s(
            sm,
            "cli:test",
            metadata={GOAL_STATE_KEY: {"status": "active", "objective": "x"}},
        )
        == 0.0
    )
    assert runner_wall_llm_timeout_s(sm, "cli:test", metadata={}) is None


def test_runner_wall_llm_timeout_reads_session_when_metadata_missing(tmp_path):
    sm = SessionManager(tmp_path)
    sess = sm.get_or_create("c:d")
    sess.metadata = {GOAL_STATE_KEY: {"status": "active", "objective": "z"}}
    assert runner_wall_llm_timeout_s(sm, "c:d") == 0.0
    sess.metadata = {}
    assert runner_wall_llm_timeout_s(sm, "c:d") is None
