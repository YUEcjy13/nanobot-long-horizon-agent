"""Session metadata helpers for sustained goals (e.g. ``long_task`` / ``complete_goal``).

Tools set ``metadata[GOAL_STATE_KEY]``. Reads accept the legacy session key ``thread_goal``
for older sessions. Callers use ``goal_state_runtime_lines``, ``goal_state_ws_blob``, and
``runner_wall_llm_timeout_s`` without importing tool implementations.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, MutableMapping

from nanobot.session.manager import SessionManager

GOAL_STATE_KEY = "goal_state"
# Older builds stored the same JSON blob under this key.
_LEGACY_GOAL_STATE_SESSION_KEY = "thread_goal"
_MAX_OBJECTIVE_IN_RUNTIME = 4000
_MAX_OBJECTIVE_WS = 600
_MAX_LINE_TEXT = 300
_MAX_PLAN_STEPS_RUNTIME = 4
_MAX_COMPLETED_STEPS_RUNTIME = 3
_MAX_FAILURES_RUNTIME = 3
_MAX_FACTS_RUNTIME = 3
_REPLAN_STALL_AFTER_S = 15 * 60


def _session_goal_raw(metadata: Mapping[str, Any] | None) -> Any:
    if not metadata:
        return None
    if GOAL_STATE_KEY in metadata:
        return metadata.get(GOAL_STATE_KEY)
    return metadata.get(_LEGACY_GOAL_STATE_SESSION_KEY)


def discard_legacy_goal_state_key(metadata: MutableMapping[str, Any]) -> None:
    """Remove legacy metadata key after migrating writes to :data:`GOAL_STATE_KEY`."""
    metadata.pop(_LEGACY_GOAL_STATE_SESSION_KEY, None)


def goal_state_raw(metadata: Mapping[str, Any] | None) -> Any:
    """Return the session goal blob under :data:`GOAL_STATE_KEY` or the legacy key."""
    return _session_goal_raw(metadata)


def sustained_goal_active(metadata: Mapping[str, Any] | None) -> bool:
    """True when this session has an active sustained objective (``long_task`` bookkeeping)."""
    goal = parse_goal_state(goal_state_raw(metadata))
    return isinstance(goal, dict) and goal.get("status") == "active"


def parse_goal_state(blob: Any) -> dict[str, Any] | None:
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def normalize_goal_text(value: Any, *, max_chars: int = _MAX_LINE_TEXT) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def normalize_goal_list(
    values: Any,
    *,
    max_items: int,
    max_chars: int = _MAX_LINE_TEXT,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        candidates = [values]
    elif isinstance(values, list):
        candidates = values
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = normalize_goal_text(item, max_chars=max_chars)
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
        if len(out) >= max_items:
            break
    return out


def _goal_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def goal_state_needs_replan(goal: Mapping[str, Any] | None) -> bool:
    return bool(goal_state_replan_reasons(goal))


def _parse_goal_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    with_timezone = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(with_timezone)
    except ValueError:
        return None


def goal_state_replan_reasons(
    goal: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_s: int = _REPLAN_STALL_AFTER_S,
) -> list[str]:
    if not isinstance(goal, Mapping):
        return []
    recent_failures = normalize_goal_list(goal.get("recent_failures"), max_items=_MAX_FAILURES_RUNTIME)
    blocked_reason = normalize_goal_text(goal.get("blocked_reason"))
    reasons: list[str] = []
    if len(recent_failures) >= 2:
        reasons.append("Repeated failures have accumulated on the active path.")
    if blocked_reason and recent_failures:
        reasons.append("The goal is blocked and already has recent failures.")

    current_step = normalize_goal_text(goal.get("current_step"))
    if current_step and stale_after_s > 0:
        anchor = _parse_goal_timestamp(goal.get("last_updated_at")) or _parse_goal_timestamp(
            goal.get("started_at")
        )
        if anchor is not None:
            age_s = max(0.0, (now or datetime.now(anchor.tzinfo)).timestamp() - anchor.timestamp())
            if age_s >= stale_after_s:
                minutes = max(1, int(age_s // 60))
                reasons.append(
                    f"The current step '{current_step}' appears stalled for about {minutes} minutes."
                )
    return reasons


def goal_state_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """Lines appended inside the Runtime Context block when a goal is active."""
    if not metadata:
        return []
    goal = parse_goal_state(_session_goal_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return []
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ["Goal: active (no objective text stored)."]
    if len(objective) > _MAX_OBJECTIVE_IN_RUNTIME:
        objective = objective[:_MAX_OBJECTIVE_IN_RUNTIME].rstrip() + "\n… (truncated)"
    out = ["Goal (active):", objective]
    hint = normalize_goal_text(goal.get("ui_summary"), max_chars=120)
    if hint:
        out.append(f"Summary: {hint}")
    current_step = normalize_goal_text(goal.get("current_step"))
    if current_step:
        out.append(f"Current Step: {current_step}")
    plan_steps = normalize_goal_list(goal.get("plan_steps"), max_items=12)
    completed_steps = normalize_goal_list(goal.get("completed_steps"), max_items=12)
    if plan_steps:
        out.append(
            f"Plan Progress: {len(completed_steps)}/{len(plan_steps)} steps completed"
        )
        pending = [step for step in plan_steps if step not in set(completed_steps)]
        if pending:
            out.append("Upcoming Steps:")
            out.extend(f"- {step}" for step in pending[:_MAX_PLAN_STEPS_RUNTIME])
    elif completed_steps:
        out.append(f"Completed Steps: {len(completed_steps)}")
    if completed_steps:
        out.append("Recently Completed:")
        out.extend(f"- {step}" for step in completed_steps[-_MAX_COMPLETED_STEPS_RUNTIME:])
    progress_summary = normalize_goal_text(goal.get("progress_summary"), max_chars=500)
    if progress_summary:
        out.append(f"Progress Summary: {progress_summary}")
    verified_facts = normalize_goal_list(goal.get("verified_facts"), max_items=_MAX_FACTS_RUNTIME)
    if verified_facts:
        out.append("Verified Facts:")
        out.extend(f"- {fact}" for fact in verified_facts)
    blocked_reason = normalize_goal_text(goal.get("blocked_reason"), max_chars=400)
    if blocked_reason:
        out.append(f"Blocked: {blocked_reason}")
    recent_failures = normalize_goal_list(goal.get("recent_failures"), max_items=_MAX_FAILURES_RUNTIME)
    if recent_failures:
        out.append("Recent Failures:")
        out.extend(f"- {item}" for item in recent_failures)
    replan_count = _goal_int(goal.get("replan_count"))
    if replan_count > 0:
        out.append(f"Replans So Far: {replan_count}")
    replan_reasons = goal_state_replan_reasons(goal)
    if replan_reasons:
        out.append(
            "Replanning Hint: repeated failures detected; revise the plan before retrying the same action."
        )
        out.extend(f"- {reason}" for reason in replan_reasons[:2])
    return out


def goal_state_ws_blob(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe snapshot for WebSocket ``goal_state`` events (one chat_id per frame)."""
    goal = parse_goal_state(_session_goal_raw(metadata)) if metadata else None
    if isinstance(goal, dict) and goal.get("status") == "active":
        objective = str(goal.get("objective") or "").strip()
        if len(objective) > _MAX_OBJECTIVE_WS:
            objective = objective[:_MAX_OBJECTIVE_WS].rstrip() + "…"
        summary = normalize_goal_text(goal.get("ui_summary"), max_chars=120)
        blob: dict[str, Any] = {"active": True}
        if summary:
            blob["ui_summary"] = summary
        if objective:
            blob["objective"] = objective
        goal_id = normalize_goal_text(goal.get("goal_id"), max_chars=80)
        if goal_id:
            blob["goal_id"] = goal_id
        current_step = normalize_goal_text(goal.get("current_step"), max_chars=180)
        if current_step:
            blob["current_step"] = current_step
        progress_summary = normalize_goal_text(goal.get("progress_summary"), max_chars=180)
        if progress_summary:
            blob["progress_summary"] = progress_summary
        blocked_reason = normalize_goal_text(goal.get("blocked_reason"), max_chars=180)
        if blocked_reason:
            blob["blocked_reason"] = blocked_reason
        plan_steps = normalize_goal_list(goal.get("plan_steps"), max_items=50)
        completed_steps = normalize_goal_list(goal.get("completed_steps"), max_items=50)
        if plan_steps:
            blob["plan_steps"] = plan_steps
            blob["total_steps"] = len(plan_steps)
        if completed_steps:
            blob["completed_steps"] = completed_steps
            blob["completed_count"] = len(completed_steps)
        verified_facts = normalize_goal_list(goal.get("verified_facts"), max_items=8, max_chars=180)
        if verified_facts:
            blob["verified_facts"] = verified_facts
        recent_failures = normalize_goal_list(goal.get("recent_failures"), max_items=5, max_chars=180)
        if recent_failures:
            blob["recent_failures"] = recent_failures
        replan_count = _goal_int(goal.get("replan_count"))
        if replan_count > 0:
            blob["replan_count"] = replan_count
        if goal_state_needs_replan(goal):
            blob["needs_replan"] = True
        return blob
    return {"active": False}


def runner_wall_llm_timeout_s(
    sessions: SessionManager,
    session_key: str | None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> float | None:
    """Wall-clock cap for :class:`~nanobot.agent.runner.AgentRunner` when streaming an LLM.

    Returns ``0.0`` to disable ``asyncio.wait_for`` around the request when a sustained goal is
    active; ``None`` means use ``NANOBOT_LLM_TIMEOUT_S``. Pass in-memory ``metadata`` when the
    caller already holds :attr:`~nanobot.session.manager.Session.metadata` for this turn.
    """
    meta: Mapping[str, Any] | None = metadata
    if meta is None and session_key:
        meta = sessions.get_or_create(session_key).metadata
    return 0.0 if sustained_goal_active(meta) else None
