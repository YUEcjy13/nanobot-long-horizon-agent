from nanobot.utils.goal_benchmark import (
    BASELINE_ENV_OVERRIDES,
    benchmark_flag_enabled,
    benchmark_variant_env,
    normalize_tool_signature,
    sanitize_frame_for_trace,
)


def test_benchmark_flag_enabled_handles_falsey_strings():
    assert benchmark_flag_enabled(None) is True
    assert benchmark_flag_enabled("0") is False
    assert benchmark_flag_enabled("false") is False
    assert benchmark_flag_enabled("off") is False
    assert benchmark_flag_enabled("yes") is True


def test_benchmark_variant_env_disables_phase_integrations_for_baseline():
    assert benchmark_variant_env("enhanced") == {}
    assert benchmark_variant_env("baseline") == BASELINE_ENV_OVERRIDES
    assert benchmark_variant_env("ablation_live") == BASELINE_ENV_OVERRIDES


def test_normalize_tool_signature_is_stable():
    left = normalize_tool_signature("read_file", {"path": "a.txt", "mode": "r"})
    right = normalize_tool_signature("read_file", {"mode": "r", "path": "a.txt"})
    assert left == right


def test_sanitize_frame_for_trace_keeps_tool_events_and_goal_state():
    message = sanitize_frame_for_trace({
        "event": "message",
        "kind": "progress",
        "tool_events": [{"phase": "start", "name": "read_file"}],
        "text": "tool trace",
    })
    assert message["event"] == "message"
    assert message["tool_events"][0]["name"] == "read_file"

    turn_end = sanitize_frame_for_trace({
        "event": "turn_end",
        "latency_ms": 1234,
        "goal_state": {"active": True, "replan_count": 1},
    })
    assert turn_end["latency_ms"] == 1234
    assert turn_end["goal_state"]["replan_count"] == 1
