"""Tests for reflective preference data export helpers."""

from __future__ import annotations

from nanobot.utils.preference_export import (
    export_preference_pairs,
    export_sft_tool_policy_successes,
)


def test_export_preference_pairs_prefers_better_variant(tmp_path):
    baseline = [
        {
            "task_id": "task-1",
            "variant": "baseline",
            "completed": False,
            "successful_replan": False,
            "repeated_failed_tool_calls": 2,
            "repeated_tool_calls": 3,
            "tool_call_count": 6,
            "latency_ms": 5000,
        }
    ]
    enhanced = [
        {
            "task_id": "task-1",
            "variant": "reflective_supervisor",
            "completed": True,
            "successful_replan": True,
            "repeated_failed_tool_calls": 0,
            "repeated_tool_calls": 1,
            "tool_call_count": 4,
            "latency_ms": 3000,
        }
    ]

    output = tmp_path / "preference_pairs.jsonl"
    rows = export_preference_pairs(baseline, enhanced, output_path=output)

    assert len(rows) == 1
    assert rows[0]["chosen_variant"] == "reflective_supervisor"
    assert output.exists()


def test_export_sft_tool_policy_successes_keeps_only_completed_traces(tmp_path):
    traces = [
        {
            "task_id": "task-1",
            "variant": "reflective_supervisor",
            "prompt": "Download the PDF.",
            "completed": True,
            "tool_calls": [{"name": "browser_download", "arguments": {"url": "https://example.com"}}],
            "final_goal_state": {"status": "completed"},
        },
        {
            "task_id": "task-2",
            "variant": "baseline",
            "prompt": "Failed task.",
            "completed": False,
            "tool_calls": [{"name": "read_file", "arguments": {"path": "missing.txt"}}],
        },
    ]

    output = tmp_path / "sft_tool_policy.jsonl"
    rows = export_sft_tool_policy_successes(traces, output_path=output)

    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["target"]["final_goal_state"]["status"] == "completed"
    assert output.exists()
