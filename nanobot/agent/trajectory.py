"""Structured trajectory tracing for long-horizon execution."""

from __future__ import annotations

import dataclasses
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from nanobot.session.goal_state import normalize_goal_text
from nanobot.utils.goal_benchmark import normalize_tool_signature
from nanobot.utils.helpers import ensure_dir

_MAX_EVENT_TEXT = 400


def _iso_now() -> str:
    return datetime.now().isoformat()


def _goal_id(goal_state: Mapping[str, Any] | None) -> str:
    return str((goal_state or {}).get("goal_id") or "").strip()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    return normalize_goal_text(value, max_chars=_MAX_EVENT_TEXT)


@dataclass
class TrajectoryEvent:
    goal_id: str
    chat_id: str | None
    turn_id: str
    event_id: str
    timestamp: str
    event_type: str
    current_step: str | None
    goal_status: str | None
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "chat_id": self.chat_id,
            "turn_id": self.turn_id,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "current_step": self.current_step,
            "goal_status": self.goal_status,
            "payload": _json_safe(self.payload),
        }


class TrajectoryTracer:
    """Append-only structured execution trace per goal."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.trace_dir = ensure_dir(workspace / "traces")

    def _goal_file(self, goal_id: str) -> Path:
        return self.trace_dir / f"trajectory_{goal_id}.jsonl"

    def append_event(
        self,
        goal_state: Mapping[str, Any] | None,
        *,
        event_type: str,
        chat_id: str | None = None,
        turn_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        goal_id = _goal_id(goal_state)
        if not goal_id:
            return None
        event = TrajectoryEvent(
            goal_id=goal_id,
            chat_id=str(chat_id or "").strip() or None,
            turn_id=str(turn_id or "").strip() or f"turn_{uuid4().hex[:12]}",
            event_id=f"evt_{uuid4().hex[:12]}",
            timestamp=_iso_now(),
            event_type=normalize_goal_text(event_type, max_chars=64) or "unknown",
            current_step=normalize_goal_text((goal_state or {}).get("current_step"), max_chars=280) or None,
            goal_status=normalize_goal_text((goal_state or {}).get("status"), max_chars=64) or None,
            payload=_json_safe(payload or {}),
        )
        row = event.to_dict()
        with open(self._goal_file(goal_id), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def read_events(self, goal_id: str) -> list[dict[str, Any]]:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return []
        rows: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self._goal_file(goal_id), "r", encoding="utf-8") as handle:
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

    def summarize_goal(self, goal_id: str) -> dict[str, Any]:
        events = self.read_events(goal_id)
        failed_events = [
            row for row in events if str(row.get("event_type") or "") == "tool_call_failed"
        ]
        tool_events = [
            row
            for row in events
            if str(row.get("event_type") or "") in {"tool_call_started", "tool_call_succeeded", "tool_call_failed"}
        ]
        failure_signatures: list[str] = []
        repeated_failed = 0
        seen_failures: set[str] = set()
        for row in failed_events:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            signature = normalize_tool_signature(
                str(payload.get("tool_name") or ""),
                payload.get("tool_args_preview") or payload.get("tool_arguments") or {},
            )
            failure_signatures.append(signature)
            if signature in seen_failures:
                repeated_failed += 1
            else:
                seen_failures.add(signature)
        return {
            "goal_id": goal_id,
            "event_count": len(events),
            "total_tool_calls": len(tool_events),
            "failed_tool_calls": len(failed_events),
            "repeated_failed_tool_calls": repeated_failed,
            "replan_events": sum(1 for row in events if row.get("event_type") == "replan_requested"),
            "reflection_events": sum(1 for row in events if row.get("event_type") == "reflection_created"),
            "completed": any(row.get("event_type") == "goal_completed" for row in events),
        }
