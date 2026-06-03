"""Run a live reflective-execution benchmark against a nanobot WebSocket gateway."""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import websockets

THIS_FILE = Path(__file__).resolve()
BENCH_DIR = THIS_FILE.parent
REPO_ROOT = BENCH_DIR.parent.parent
PROJECT_ROOT = REPO_ROOT.parent
TASKS_FILE = BENCH_DIR / "tasks.json"
WORKSPACE = PROJECT_ROOT / "nanobot_runtime_workspace"
MEMORY_FILE = WORKSPACE / "memory" / "execution_memory.jsonl"
TRACE_DIR = WORKSPACE / "traces"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


goal_benchmark = _load_module("goal_benchmark", REPO_ROOT / "nanobot" / "utils" / "goal_benchmark.py")
goal_eval = _load_module("goal_eval", REPO_ROOT / "nanobot" / "utils" / "goal_eval.py")
trajectory_eval = _load_module("trajectory_eval", REPO_ROOT / "nanobot" / "utils" / "trajectory_eval.py")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="reflective_supervisor", help="Variant label written into benchmark records.")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8765", help="Running nanobot WebSocket URL.")
    parser.add_argument("--tasks-file", default=str(TASKS_FILE), help="Reflective benchmark task definition JSON.")
    parser.add_argument("--record-output", default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "records.jsonl"))
    parser.add_argument("--trace-output", default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "traces.jsonl"))
    parser.add_argument("--metrics-json", default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "metrics.json"))
    parser.add_argument("--metrics-csv", default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "metrics.csv"))
    parser.add_argument("--summary-md", default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "summary.md"))
    parser.add_argument("--max-tasks", type=int, default=0, help="Optional limit for quick smoke runs.")
    return parser.parse_args()


def _blank_record(task: dict[str, Any], variant: str) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "variant": variant,
        "category": task.get("category", ""),
        "evidence": "live",
        "completed": False,
        "tool_call_count": 0,
        "repeated_tool_calls": 0,
        "had_failure": False,
        "recovered_after_failure": False,
        "step_count": 0,
        "replan_count": 0,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def _load_tasks(path: str) -> list[dict[str, Any]]:
    tasks = goal_benchmark.load_benchmark_tasks(path)
    return tasks


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return goal_benchmark.load_jsonl_records(path)


def _read_execution_memory() -> list[dict[str, Any]]:
    return _read_jsonl(MEMORY_FILE)


def _read_goal_trajectory(goal_id: str | None) -> list[dict[str, Any]]:
    if not goal_id:
        return []
    trace_file = TRACE_DIR / f"trajectory_{goal_id}.jsonl"
    return _read_jsonl(trace_file)


def _resolve_goal_id(
    final_goal_state: dict[str, Any] | None,
    execution_entries: list[dict[str, Any]],
) -> str:
    goal = dict(final_goal_state or {})
    goal_id = str(goal.get("goal_id") or "").strip()
    if goal_id:
        return goal_id
    goal_ids = {
        str(entry.get("goal_id") or "").strip()
        for entry in execution_entries
        if str(entry.get("goal_id") or "").strip()
    }
    if len(goal_ids) == 1:
        return next(iter(goal_ids))
    trace_files = sorted(TRACE_DIR.glob("trajectory_*.jsonl"))
    if len(trace_files) == 1:
        return trace_files[0].stem.removeprefix("trajectory_")
    return ""


def _cleanup_runtime_artifacts() -> None:
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()
    if TRACE_DIR.exists():
        for path in TRACE_DIR.glob("trajectory_*.jsonl"):
            path.unlink()


def _seed_workspace_files(task: dict[str, Any]) -> None:
    _seed_workspace_rows(task.get("setup_files") or [])


def _seed_workspace_rows(rows: list[dict[str, Any]] | None) -> None:
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        target = Path(str(row.get("path") or "")).expanduser()
        if not target.is_absolute():
            target = WORKSPACE / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(row.get("content") or ""), encoding="utf-8")


def _ingest_tool_event(
    record: dict[str, Any],
    trace: dict[str, Any],
    state: dict[str, Any],
    event: dict[str, Any],
) -> None:
    phase = str(event.get("phase") or "").strip().lower()
    call_id = str(event.get("call_id") or "").strip()
    name = str(event.get("name") or "").strip()
    arguments = event.get("arguments") or {}
    signature = goal_benchmark.normalize_tool_signature(name, arguments)
    dedupe_key = call_id or signature
    if phase in {"start", "end", "error"}:
        if dedupe_key in state["started_call_ids"]:
            if phase == "error":
                record["had_failure"] = True
                trace["tool_errors"].append({
                    "call_id": call_id,
                    "name": name,
                    "error": event.get("error"),
                })
            return
        state["started_call_ids"].add(dedupe_key)
        record["tool_call_count"] += 1
        state["tool_signature_counts"][signature] += 1
        if state["tool_signature_counts"][signature] > 1:
            record["repeated_tool_calls"] += 1
        trace["tool_calls"].append({
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
            "phase": phase,
        })
    if phase == "error":
        record["had_failure"] = True
        trace["tool_errors"].append({
            "call_id": call_id,
            "name": name,
            "error": event.get("error"),
        })


def _ingest_frame(
    record: dict[str, Any],
    trace: dict[str, Any],
    state: dict[str, Any],
    frame: dict[str, Any],
) -> tuple[bool, bool]:
    event = str(frame.get("event") or "")
    trace["frames"].append(goal_benchmark.sanitize_frame_for_trace(frame))

    if event == "message":
        for tool_event in frame.get("tool_events") or []:
            if isinstance(tool_event, dict):
                _ingest_tool_event(record, trace, state, tool_event)
        return False, False

    if event == "goal_state":
        goal_state = frame.get("goal_state")
        if isinstance(goal_state, dict):
            trace["goal_states"].append(goal_state)
            record["replan_count"] = max(record["replan_count"], int(goal_state.get("replan_count") or 0))
        return False, False

    if event == "turn_end":
        state["turn_count"] += 1
        record["step_count"] = state["turn_count"]
        record["latency_ms"] += int(frame.get("latency_ms") or 0)
        goal_state = frame.get("goal_state")
        if isinstance(goal_state, dict):
            trace["goal_states"].append(goal_state)
            record["replan_count"] = max(record["replan_count"], int(goal_state.get("replan_count") or 0))
            if not goal_state.get("active", False):
                record["completed"] = True
                return True, False
            return False, True
        record["completed"] = True
        return True, False

    if event == "error":
        trace["errors"].append(str(frame.get("detail") or "unknown websocket error"))
        return True, False

    return False, False


async def _run_task(
    task: dict[str, Any],
    *,
    variant: str,
    ws_url: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _blank_record(task, variant)
    trace = {
        "task_id": task["task_id"],
        "variant": variant,
        "category": task.get("category", ""),
        "prompt": task["prompt"],
        "success_criteria": task.get("success_criteria", []),
        "chat_id": f"reflective-{uuid.uuid4().hex[:8]}",
        "frames": [],
        "tool_calls": [],
        "tool_errors": [],
        "goal_states": [],
        "trajectory_events": [],
        "errors": [],
    }
    state = {
        "started_call_ids": set(),
        "tool_signature_counts": Counter(),
        "turn_count": 0,
    }
    trace["started_at"] = time.time()

    _cleanup_runtime_artifacts()
    _seed_workspace_files(task)
    max_turns = max(1, int(task.get("max_turns") or 6))

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"event": "subscribe", "chat_id": trace["chat_id"]}))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "event": "message",
            "chat_id": trace["chat_id"],
            "message": task["prompt"],
        }))

        async for raw in ws:
            frame = json.loads(raw)
            done, needs_follow_up = _ingest_frame(record, trace, state, frame)
            if done:
                break
            if needs_follow_up:
                if state["turn_count"] >= max_turns:
                    trace["errors"].append(f"turn limit reached ({max_turns})")
                    break
                _seed_workspace_rows(
                    goal_benchmark.benchmark_follow_up_setup_files(task, state["turn_count"])
                )
                await ws.send(json.dumps({
                    "event": "message",
                    "chat_id": trace["chat_id"],
                    "message": goal_benchmark.resolve_benchmark_follow_up(task, state["turn_count"]),
                }))

    execution_entries = _read_execution_memory()
    final_goal_state = trace["goal_states"][-1] if trace["goal_states"] else {}
    goal_id = _resolve_goal_id(final_goal_state, execution_entries)
    trajectory_events = _read_goal_trajectory(goal_id)
    trace["execution_memory_entries"] = execution_entries
    trace["trajectory_events"] = trajectory_events
    trace["final_goal_state"] = final_goal_state

    if any(
        str(entry.get("status") or "").lower() == "failure"
        or str(entry.get("event_type") or "").lower() == "failure"
        for entry in execution_entries
    ):
        record["had_failure"] = True
    if record["completed"] and record["had_failure"]:
        record["recovered_after_failure"] = True

    enriched = trajectory_eval.enrich_reflective_record(
        record,
        trajectory_events,
        final_goal_state=final_goal_state,
    )
    trace["ended_at"] = time.time()
    trace["elapsed_s"] = round(trace["ended_at"] - trace["started_at"], 3)
    trace["completed"] = bool(enriched.get("completed"))
    return enriched, trace


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_metrics_csv(path: Path, summary: dict[str, dict[str, float | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metric_order = [
        "task_count",
        "task_success_rate",
        "step_completion_rate",
        "repeated_tool_call_rate",
        "repeated_failed_tool_call_rate",
        "replan_trigger_count",
        "successful_replan_rate",
        "total_reflection_count",
        "average_reflection_count",
        "reflection_reuse_rate",
        "avg_tool_calls",
        "avg_turns",
        "avg_latency_ms",
        "trajectory_completeness",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["variant", *metric_order])
        for variant, metrics in summary.items():
            writer.writerow([variant, *[metrics.get(key, 0) for key in metric_order]])


def _render_summary(summary: dict[str, dict[str, float | int]], *, variant: str, record_output: str) -> str:
    lines = [
        "# Reflective Execution Benchmark Summary",
        "",
        f"- Variant: `{variant}`",
        f"- Records: `{record_output}`",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> None:
    tasks = _load_tasks(args.tasks_file)
    if args.max_tasks and args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]

    records: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for task in tasks:
        record, trace = await _run_task(task, variant=args.variant, ws_url=args.ws_url)
        records.append(record)
        traces.append(trace)

    summary = trajectory_eval.aggregate_reflective_eval(records)
    record_output = Path(args.record_output)
    trace_output = Path(args.trace_output)
    metrics_json = Path(args.metrics_json)
    metrics_csv = Path(args.metrics_csv)
    summary_md = Path(args.summary_md)

    _write_jsonl(record_output, records)
    _write_jsonl(trace_output, traces)
    metrics_json.parent.mkdir(parents=True, exist_ok=True)
    metrics_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_metrics_csv(metrics_csv, summary)
    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_md.write_text(_render_summary(summary, variant=args.variant, record_output=str(record_output)) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Records written to: {record_output}")
    print(f"Traces written to: {trace_output}")
    print(f"Metrics JSON written to: {metrics_json}")
    print(f"Metrics CSV written to: {metrics_csv}")
    print(f"Summary Markdown written to: {summary_md}")


def main() -> None:
    asyncio.run(_main_async(_parse_args()))


if __name__ == "__main__":
    main()
