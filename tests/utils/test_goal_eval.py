from nanobot.utils.goal_eval import aggregate_goal_eval, normalize_goal_eval_record


def test_normalize_goal_eval_record_fills_defaults():
    row = normalize_goal_eval_record({"task_id": "t1", "completed": True})
    assert row["variant"] == "unknown"
    assert row["tool_call_count"] == 0
    assert row["repeated_tool_calls"] == 0
    assert row["completed"] is True


def test_aggregate_goal_eval_computes_variant_metrics():
    summary = aggregate_goal_eval([
        {
            "task_id": "task-1",
            "variant": "baseline",
            "completed": False,
            "tool_call_count": 4,
            "repeated_tool_calls": 2,
            "had_failure": True,
            "recovered_after_failure": False,
            "step_count": 6,
            "replan_count": 0,
            "latency_ms": 1000,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
        {
            "task_id": "task-2",
            "variant": "enhanced",
            "completed": True,
            "tool_call_count": 5,
            "repeated_tool_calls": 1,
            "had_failure": True,
            "recovered_after_failure": True,
            "step_count": 5,
            "replan_count": 1,
            "latency_ms": 1200,
            "prompt_tokens": 120,
            "completion_tokens": 60,
        },
        {
            "task_id": "task-3",
            "variant": "enhanced",
            "completed": True,
            "tool_call_count": 3,
            "repeated_tool_calls": 0,
            "had_failure": False,
            "recovered_after_failure": False,
            "step_count": 4,
            "replan_count": 0,
            "latency_ms": 800,
            "prompt_tokens": 80,
            "completion_tokens": 40,
        },
    ])

    assert summary["baseline"]["completion_rate"] == 0.0
    assert summary["baseline"]["repeated_tool_call_rate"] == 0.5
    assert summary["enhanced"]["completion_rate"] == 1.0
    assert summary["enhanced"]["failure_recovery_rate"] == 1.0
    assert summary["enhanced"]["average_step_count"] == 4.5
    assert summary["enhanced"]["average_replan_count"] == 0.5
