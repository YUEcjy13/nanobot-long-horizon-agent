"""Helpers for reflective execution trajectory evaluation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from nanobot.utils.goal_benchmark import normalize_tool_signature
from nanobot.utils.goal_eval import normalize_goal_eval_record


def summarize_trajectory_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    def _mark_replanned(row: dict[str, Any]) -> bool:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        updates = payload.get("updates")
        if not isinstance(updates, dict):
            return False
        return bool(updates.get("mark_replanned"))

    counts: dict[str, int] = defaultdict(int)
    failed_signatures: set[str] = set()
    repeated_failed_tool_calls = 0
    reflection_indices: list[int] = []
    reflection_reuse_count = 0
    replan_indices: list[int] = []
    successful_replan_count = 0

    for idx, row in enumerate(events):
        event_type = str(row.get("event_type") or "").strip()
        counts[event_type] += 1
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if event_type == "tool_call_failed":
            signature = normalize_tool_signature(
                str(payload.get("tool_name") or ""),
                payload.get("tool_args_preview") or payload.get("tool_arguments") or {},
            )
            if signature in failed_signatures:
                repeated_failed_tool_calls += 1
            else:
                failed_signatures.add(signature)
        if event_type == "reflection_created":
            reflection_indices.append(idx)
        if event_type == "replan_requested":
            replan_indices.append(idx)

    for idx in reflection_indices:
        tail = events[idx + 1 :]
        if any(
            str(row.get("event_type") or "") in {"goal_state_updated", "goal_completed"}
            for row in tail
        ):
            reflection_reuse_count += 1

    for idx in replan_indices:
        tail = events[idx + 1 :]
        if any(
            str(row.get("event_type") or "") == "goal_state_updated"
            and _mark_replanned(row)
            for row in tail
        ) or any(str(row.get("event_type") or "") == "goal_completed" for row in tail):
            successful_replan_count += 1

    expected_signals = {
        "goal_started": counts.get("goal_started", 0) > 0,
        "tool_telemetry": (counts.get("tool_call_succeeded", 0) + counts.get("tool_call_failed", 0)) > 0,
        "goal_update": counts.get("goal_state_updated", 0) > 0,
        "verifier": counts.get("verifier_decision", 0) > 0 or counts.get("replan_requested", 0) > 0,
        "reflection": counts.get("reflection_created", 0) > 0 or counts.get("replan_requested", 0) > 0,
        "completion": counts.get("goal_completed", 0) > 0,
    }
    required = 0
    present = 0
    for key, condition in expected_signals.items():
        if key == "completion" and not condition:
            continue
        if key == "reflection" and not (
            counts.get("tool_call_failed", 0) > 0 or counts.get("replan_requested", 0) > 0
        ):
            continue
        required += 1
        if condition:
            present += 1

    return {
        "event_count": len(events),
        "total_tool_calls": counts.get("tool_call_succeeded", 0) + counts.get("tool_call_failed", 0),
        "failed_tool_calls": counts.get("tool_call_failed", 0),
        "repeated_failed_tool_calls": repeated_failed_tool_calls,
        "verifier_decision_count": counts.get("verifier_decision", 0),
        "reflection_count": counts.get("reflection_created", 0),
        "reflection_reuse_count": reflection_reuse_count,
        "replan_trigger_count": counts.get("replan_requested", 0),
        "successful_replan_count": successful_replan_count,
        "completed": counts.get("goal_completed", 0) > 0,
        "trajectory_completeness": (present / required) if required else 1.0,
    }


def enrich_reflective_record(
    record: dict[str, Any],
    trajectory_events: list[dict[str, Any]],
    *,
    final_goal_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(record)
    summary = summarize_trajectory_events(trajectory_events)
    goal = dict(final_goal_state or {})
    plan_steps = goal.get("plan_steps") if isinstance(goal.get("plan_steps"), list) else []
    completed_steps = goal.get("completed_steps") if isinstance(goal.get("completed_steps"), list) else []
    total_plan_steps = len(plan_steps)
    completed_lookup = {str(step).strip() for step in completed_steps if str(step).strip()}
    completed_plan_steps = sum(1 for step in plan_steps if str(step).strip() in completed_lookup)

    enriched["replan_count"] = max(
        int(enriched.get("replan_count") or 0),
        int(summary["replan_trigger_count"]),
    )
    enriched["repeated_failed_tool_calls"] = int(summary["repeated_failed_tool_calls"])
    enriched["failed_tool_call_count"] = int(summary["failed_tool_calls"])
    enriched["reflection_count"] = int(summary["reflection_count"])
    enriched["reflection_reuse_count"] = int(summary["reflection_reuse_count"])
    enriched["trajectory_event_count"] = int(summary["event_count"])
    enriched["trajectory_completeness"] = float(summary["trajectory_completeness"])
    enriched["verifier_decision_count"] = int(summary["verifier_decision_count"])
    enriched["replan_trigger_count"] = int(summary["replan_trigger_count"])
    enriched["successful_replan"] = bool(
        summary["successful_replan_count"] > 0
        or (bool(enriched.get("completed")) and int(enriched.get("replan_count") or 0) > 0)
    )
    enriched["total_plan_steps"] = total_plan_steps
    enriched["completed_plan_steps"] = completed_plan_steps
    enriched["step_completion_rate"] = (
        completed_plan_steps / total_plan_steps if total_plan_steps else 0.0
    )
    return enriched


def normalize_reflective_eval_record(record: dict[str, Any]) -> dict[str, Any]:
    base = normalize_goal_eval_record(record)
    if "replan_trigger_count" in record:
        replan_trigger_count = record.get("replan_trigger_count")
    else:
        replan_trigger_count = record.get("replan_count")
    return {
        **base,
        "repeated_failed_tool_calls": max(0, int(record.get("repeated_failed_tool_calls") or 0)),
        "failed_tool_call_count": max(0, int(record.get("failed_tool_call_count") or 0)),
        "reflection_count": max(0, int(record.get("reflection_count") or 0)),
        "reflection_reuse_count": max(0, int(record.get("reflection_reuse_count") or 0)),
        "trajectory_event_count": max(0, int(record.get("trajectory_event_count") or 0)),
        "trajectory_completeness": max(0.0, min(1.0, float(record.get("trajectory_completeness") or 0.0))),
        "verifier_decision_count": max(0, int(record.get("verifier_decision_count") or 0)),
        "replan_trigger_count": max(0, int(replan_trigger_count or 0)),
        "successful_replan": bool(record.get("successful_replan")),
        "total_plan_steps": max(0, int(record.get("total_plan_steps") or 0)),
        "completed_plan_steps": max(0, int(record.get("completed_plan_steps") or 0)),
        "step_completion_rate": max(0.0, min(1.0, float(record.get("step_completion_rate") or 0.0))),
    }


def aggregate_reflective_eval(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        normalized = normalize_reflective_eval_record(row)
        groups[normalized["variant"]].append(normalized)

    summary: dict[str, dict[str, float | int]] = {}
    for variant, rows in groups.items():
        total = len(rows)
        tool_calls = sum(int(row["tool_call_count"]) for row in rows)
        failed_tool_calls = sum(int(row["failed_tool_call_count"]) for row in rows)
        total_failed_tool_events = sum(int(row["repeated_failed_tool_calls"]) for row in rows)
        total_replans = sum(int(row["replan_trigger_count"]) for row in rows)
        replan_rows = [row for row in rows if int(row["replan_trigger_count"]) > 0]
        reflections = sum(int(row["reflection_count"]) for row in rows)
        reflection_reuse = sum(int(row["reflection_reuse_count"]) for row in rows)
        total_plan_steps = sum(int(row["total_plan_steps"]) for row in rows)
        completed_plan_steps = sum(int(row["completed_plan_steps"]) for row in rows)

        summary[variant] = {
            "task_count": total,
            "task_success_rate": (sum(1 for row in rows if row["completed"]) / total) if total else 0.0,
            "completion_rate": (sum(1 for row in rows if row["completed"]) / total) if total else 0.0,
            "step_completion_rate": (
                completed_plan_steps / total_plan_steps if total_plan_steps else 0.0
            ),
            "repeated_tool_call_rate": (
                sum(int(row["repeated_tool_calls"]) for row in rows) / tool_calls if tool_calls else 0.0
            ),
            "repeated_failed_tool_call_rate": (
                total_failed_tool_events / failed_tool_calls if failed_tool_calls else 0.0
            ),
            "replan_trigger_count": total_replans,
            "successful_replan_rate": (
                sum(1 for row in replan_rows if row["successful_replan"]) / len(replan_rows)
                if replan_rows
                else 0.0
            ),
            "total_reflection_count": reflections,
            "average_reflection_count": (reflections / total) if total else 0.0,
            "reflection_reuse_rate": (
                reflection_reuse / reflections if reflections else 0.0
            ),
            "avg_tool_calls": (tool_calls / total) if total else 0.0,
            "avg_turns": (
                sum(int(row["step_count"]) for row in rows) / total if total else 0.0
            ),
            "avg_latency_ms": (
                sum(int(row["latency_ms"]) for row in rows) / total if total else 0.0
            ),
            "trajectory_completeness": (
                sum(float(row["trajectory_completeness"]) for row in rows) / total if total else 0.0
            ),
        }
    return summary
