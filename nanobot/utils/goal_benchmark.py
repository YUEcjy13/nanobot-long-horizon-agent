"""Helpers for collecting reproducible goal-execution benchmark evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FALSEY = {"0", "false", "off", "no"}

BASELINE_ENV_OVERRIDES = {
    "NANOBOT_ENABLE_GOAL_RUNTIME_CONTEXT": "0",
    "NANOBOT_ENABLE_EXECUTION_RECALL": "0",
    "NANOBOT_ENABLE_AUTO_REPLAN": "0",
}

SUPERVISOR_ENV_OVERRIDES = {
    "NANOBOT_ENABLE_EXECUTION_VERIFIER": "1",
    "NANOBOT_ENABLE_REFLECTION_MEMORY": "1",
}


def benchmark_flag_enabled(value: str | None, *, default: bool = True) -> bool:
    """Interpret common environment-flag spellings."""
    if value is None:
        return default
    return value.strip().lower() not in _FALSEY


def benchmark_variant_env(variant: str) -> dict[str, str]:
    """Return gateway env overrides for a benchmark variant."""
    normalized = str(variant or "").strip().lower()
    if normalized in {"baseline", "baseline_live", "ablation", "ablation_live"}:
        return dict(BASELINE_ENV_OVERRIDES)
    if normalized in {"reflective_supervisor", "supervisor", "reflective"}:
        return dict(SUPERVISOR_ENV_OVERRIDES)
    return {}


def load_benchmark_tasks(path: str | Path) -> list[dict[str, Any]]:
    """Load canonical benchmark task definitions from JSON."""
    task_path = Path(path)
    raw = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a task list in {task_path}")
    tasks: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not task_id or not prompt:
            continue
        tasks.append(item)
    if not tasks:
        raise ValueError(f"No runnable tasks found in {task_path}")
    return tasks


def resolve_benchmark_follow_up(task: dict[str, Any], turn_count: int) -> str:
    """Resolve the follow-up prompt for the next turn.

    Turn counts are 1-based here: after the first completed turn, ``turn_count`` is 1,
    so the first entry of ``follow_up_prompts`` is selected.
    """
    prompts = task.get("follow_up_prompts")
    if isinstance(prompts, list):
        normalized = [str(item).strip() for item in prompts if str(item).strip()]
        if normalized:
            index = min(max(0, int(turn_count) - 1), len(normalized) - 1)
            return normalized[index]
    prompt = str(task.get("follow_up_prompt") or "").strip()
    if prompt:
        return prompt
    return "Continue working on the current long-running task until it is complete."


def benchmark_follow_up_setup_files(task: dict[str, Any], turn_count: int) -> list[dict[str, Any]]:
    """Resolve staged workspace files for the next follow-up turn."""
    stages = task.get("follow_up_setup_files")
    if not isinstance(stages, list) or not stages:
        return []
    index = min(max(0, int(turn_count) - 1), len(stages) - 1)
    rows = stages[index]
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def normalize_tool_signature(name: str, arguments: Any) -> str:
    """Build a stable duplicate-detection signature for a tool call."""
    normalized_name = str(name or "").strip() or "(unknown)"
    try:
        arg_text = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except TypeError:
        arg_text = json.dumps(str(arguments), ensure_ascii=False)
    return f"{normalized_name}:{arg_text}"


def sanitize_frame_for_trace(frame: dict[str, Any]) -> dict[str, Any]:
    """Keep only benchmark-relevant frame fields in trace artifacts."""
    event = str(frame.get("event") or "")
    body: dict[str, Any] = {"event": event}
    if event == "message":
        if frame.get("kind"):
            body["kind"] = frame.get("kind")
        if frame.get("tool_events"):
            body["tool_events"] = frame.get("tool_events")
        text = str(frame.get("text") or "").strip()
        if text:
            body["text"] = text[:400]
    elif event in {"goal_state", "turn_end"}:
        goal_state = frame.get("goal_state")
        if isinstance(goal_state, dict):
            body["goal_state"] = goal_state
        if event == "turn_end" and frame.get("latency_ms") is not None:
            body["latency_ms"] = int(frame.get("latency_ms") or 0)
    elif event == "error":
        body["detail"] = str(frame.get("detail") or "")
    else:
        for key in ("status", "started_at", "text"):
            if key in frame:
                body[key] = frame[key]
    return body


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    """Read JSONL records while skipping malformed rows."""
    rows: list[dict[str, Any]] = []
    file_path = Path(path)
    if not file_path.exists():
        return rows
    with open(file_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows
