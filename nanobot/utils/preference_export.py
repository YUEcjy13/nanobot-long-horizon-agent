"""Export preference and SFT data from reflective benchmark artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _score_record(record: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    return (
        1 if bool(record.get("completed")) else 0,
        1 if bool(record.get("successful_replan")) else 0,
        -int(record.get("repeated_failed_tool_calls") or 0),
        -int(record.get("repeated_tool_calls") or 0),
        -int(record.get("tool_call_count") or 0),
        -int(record.get("latency_ms") or 0),
    )


def export_preference_pairs(
    baseline_records: list[dict[str, Any]],
    enhanced_records: list[dict[str, Any]],
    *,
    output_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    by_task = {str(row.get("task_id") or "").strip(): row for row in baseline_records}
    pairs: list[dict[str, Any]] = []
    for enhanced in enhanced_records:
        task_id = str(enhanced.get("task_id") or "").strip()
        baseline = by_task.get(task_id)
        if not task_id or baseline is None:
            continue
        left = baseline
        right = enhanced
        if _score_record(right) < _score_record(left):
            left, right = right, left
        if _score_record(left) == _score_record(right):
            continue
        pair = {
            "task_id": task_id,
            "category": str(enhanced.get("category") or baseline.get("category") or "").strip(),
            "chosen_variant": str(right.get("variant") or "").strip() or "chosen",
            "rejected_variant": str(left.get("variant") or "").strip() or "rejected",
            "chosen_metrics": {
                "completed": bool(right.get("completed")),
                "successful_replan": bool(right.get("successful_replan")),
                "repeated_failed_tool_calls": int(right.get("repeated_failed_tool_calls") or 0),
                "repeated_tool_calls": int(right.get("repeated_tool_calls") or 0),
                "tool_call_count": int(right.get("tool_call_count") or 0),
                "latency_ms": int(right.get("latency_ms") or 0),
            },
            "rejected_metrics": {
                "completed": bool(left.get("completed")),
                "successful_replan": bool(left.get("successful_replan")),
                "repeated_failed_tool_calls": int(left.get("repeated_failed_tool_calls") or 0),
                "repeated_tool_calls": int(left.get("repeated_tool_calls") or 0),
                "tool_call_count": int(left.get("tool_call_count") or 0),
                "latency_ms": int(left.get("latency_ms") or 0),
            },
            "preference_reason": "Prefer trajectories with successful completion, stronger recovery, fewer repeated failures, and lower execution cost.",
        }
        pairs.append(pair)

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for row in pairs:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return pairs


def export_sft_tool_policy_successes(
    traces: list[dict[str, Any]],
    *,
    output_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trace in traces:
        if not bool(trace.get("completed")):
            continue
        tool_calls = trace.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        row = {
            "task_id": str(trace.get("task_id") or "").strip(),
            "variant": str(trace.get("variant") or "").strip(),
            "instruction": str(trace.get("prompt") or "").strip(),
            "target": {
                "tool_calls": tool_calls,
                "final_goal_state": trace.get("final_goal_state") or {},
            },
        }
        rows.append(row)

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows
