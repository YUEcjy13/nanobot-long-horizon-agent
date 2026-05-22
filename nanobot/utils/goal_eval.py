"""Utilities for lightweight goal-execution benchmark aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def normalize_goal_eval_record(record: dict[str, Any]) -> dict[str, Any]:
    """Fill optional benchmark fields with safe defaults."""
    return {
        "task_id": str(record.get("task_id") or "").strip(),
        "variant": str(record.get("variant") or "unknown").strip() or "unknown",
        "completed": bool(record.get("completed")),
        "tool_call_count": max(0, int(record.get("tool_call_count") or 0)),
        "repeated_tool_calls": max(0, int(record.get("repeated_tool_calls") or 0)),
        "had_failure": bool(record.get("had_failure")),
        "recovered_after_failure": bool(record.get("recovered_after_failure")),
        "step_count": max(0, int(record.get("step_count") or 0)),
        "replan_count": max(0, int(record.get("replan_count") or 0)),
        "latency_ms": max(0, int(record.get("latency_ms") or 0)),
        "prompt_tokens": max(0, int(record.get("prompt_tokens") or 0)),
        "completion_tokens": max(0, int(record.get("completion_tokens") or 0)),
    }


def aggregate_goal_eval(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """Aggregate benchmark records by variant.

    Expected per-task record fields:
    ``task_id``, ``variant``, ``completed``, ``tool_call_count``,
    ``repeated_tool_calls``, ``had_failure``, ``recovered_after_failure``,
    ``step_count``, ``replan_count``, ``latency_ms``, ``prompt_tokens``,
    and ``completion_tokens``.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        normalized = normalize_goal_eval_record(raw)
        groups[normalized["variant"]].append(normalized)

    summary: dict[str, dict[str, float | int]] = {}
    for variant, rows in groups.items():
        total = len(rows)
        tool_calls = sum(int(row["tool_call_count"]) for row in rows)
        repeated_calls = sum(int(row["repeated_tool_calls"]) for row in rows)
        failure_rows = [row for row in rows if row["had_failure"]]
        recovered_rows = [row for row in failure_rows if row["recovered_after_failure"]]
        total_tokens = sum(int(row["prompt_tokens"]) + int(row["completion_tokens"]) for row in rows)

        summary[variant] = {
            "task_count": total,
            "completion_rate": (sum(1 for row in rows if row["completed"]) / total) if total else 0.0,
            "repeated_tool_call_rate": (repeated_calls / tool_calls) if tool_calls else 0.0,
            "failure_recovery_rate": (
                len(recovered_rows) / len(failure_rows)
                if failure_rows
                else 0.0
            ),
            "average_step_count": (
                sum(int(row["step_count"]) for row in rows) / total
                if total
                else 0.0
            ),
            "average_replan_count": (
                sum(int(row["replan_count"]) for row in rows) / total
                if total
                else 0.0
            ),
            "average_latency_ms": (
                sum(int(row["latency_ms"]) for row in rows) / total
                if total
                else 0.0
            ),
            "average_total_tokens": (total_tokens / total) if total else 0.0,
        }
    return summary
