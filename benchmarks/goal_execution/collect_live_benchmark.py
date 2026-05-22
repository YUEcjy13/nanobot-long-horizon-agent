"""Run a live benchmark against a running nanobot WebSocket gateway."""

from __future__ import annotations

import argparse
import asyncio
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


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


goal_eval = _load_module("goal_eval", REPO_ROOT / "nanobot" / "utils" / "goal_eval.py")
goal_benchmark = _load_module("goal_benchmark", REPO_ROOT / "nanobot" / "utils" / "goal_benchmark.py")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="enhanced", help="Variant label written into benchmark records.")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8765", help="Running nanobot WebSocket URL.")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "benchmark_records_enhanced.jsonl"),
        help="JSONL output path for normalized benchmark records.",
    )
    parser.add_argument(
        "--trace-output",
        default=str(PROJECT_ROOT / "benchmark_traces_enhanced.jsonl"),
        help="JSONL output path for raw per-task traces.",
    )
    parser.add_argument(
        "--tasks-file",
        default=str(TASKS_FILE),
        help="Canonical benchmark task definition JSON.",
    )
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


def _read_execution_memory() -> list[dict[str, Any]]:
    return goal_benchmark.load_jsonl_records(MEMORY_FILE)


def _seed_workspace_files(task: dict[str, Any]) -> None:
    for row in task.get("setup_files") or []:
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
        "chat_id": f"bench-{uuid.uuid4().hex[:8]}",
        "frames": [],
        "tool_calls": [],
        "tool_errors": [],
        "goal_states": [],
        "errors": [],
    }
    state = {
        "started_call_ids": set(),
        "tool_signature_counts": Counter(),
        "turn_count": 0,
    }
    trace["started_at"] = time.time()

    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()
    _seed_workspace_files(task)

    max_turns = max(1, int(task.get("max_turns") or 6))
    follow_up = str(task.get("follow_up_prompt") or "Continue working on the current task until it is complete.").strip()

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"event": "subscribe", "chat_id": trace["chat_id"]}))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "event": "message",
            "chat_id": trace["chat_id"],
            "message": task["prompt"],
        }))

        should_stop = False
        async for raw in ws:
            frame = json.loads(raw)
            done, needs_follow_up = _ingest_frame(record, trace, state, frame)
            if done:
                should_stop = True
                break
            if needs_follow_up:
                if state["turn_count"] >= max_turns:
                    trace["errors"].append(f"turn limit reached ({max_turns})")
                    break
                await ws.send(json.dumps({
                    "event": "message",
                    "chat_id": trace["chat_id"],
                    "message": follow_up,
                }))
        if not should_stop and trace["errors"] and not record["completed"]:
            record["completed"] = False

    execution_entries = _read_execution_memory()
    trace["execution_memory_entries"] = execution_entries
    record["_execution_memory_entries"] = len(execution_entries)
    record["_tool_error_count"] = len(trace["tool_errors"])
    if any(
        str(entry.get("status") or "").lower() == "failure"
        or str(entry.get("event_type") or "").lower() == "failure"
        for entry in execution_entries
    ):
        record["had_failure"] = True
    if record["completed"] and record["had_failure"]:
        record["recovered_after_failure"] = True

    trace["ended_at"] = time.time()
    trace["elapsed_s"] = round(trace["ended_at"] - trace["started_at"], 3)
    return record, trace


async def _main() -> None:
    args = _parse_args()
    tasks = goal_benchmark.load_benchmark_tasks(args.tasks_file)
    results: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []

    for task in tasks:
        print("=" * 60)
        print(f"Task: {task['task_id']}")
        print(f"Variant: {args.variant}")
        print(f"Prompt: {task['prompt'][:180]}...")
        record, trace = await _run_task(task, variant=args.variant, ws_url=args.ws_url)
        results.append(record)
        traces.append(trace)
        print(
            "  Result:"
            f" completed={record['completed']}"
            f" tool_calls={record['tool_call_count']}"
            f" repeated={record['repeated_tool_calls']}"
            f" failures={record['had_failure']}"
            f" recovered={record['recovered_after_failure']}"
            f" replans={record['replan_count']}"
            f" turns={record['step_count']}"
            f" latency_ms={record['latency_ms']}"
        )

    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
        encoding="utf-8",
    )
    trace_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in traces),
        encoding="utf-8",
    )

    summary = goal_eval.aggregate_goal_eval(results)
    print("\nAggregated summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nRecords written to: {output_path}")
    print(f"Traces written to:  {trace_path}")


if __name__ == "__main__":
    asyncio.run(_main())
