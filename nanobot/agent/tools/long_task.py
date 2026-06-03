"""Sustained goal tools on the main agent (Codex-style).

Follow the built-in **long-goal** skill for lifecycle rules and how to phrase
objectives (especially **idempotent**, compaction-safe goals). Load that skill
from the skills listing (path shown there) before composing ``long_task.goal`` text.

``long_task`` registers an objective on the session (JSON-serializable metadata).
Active objectives are mirrored each turn into the Runtime Context block (see
``nanobot.session.goal_state.goal_state_runtime_lines``) so compaction cannot hide them.
Work proceeds in ordinary agent turns (same runner, compaction as configured).
Call ``complete_goal`` when the sustained objective should stop being tracked:
finished successfully, or cancelled / superseded / redirected—in every case the recap should match reality.

There is **no** sub-agent orchestrator and **no** special WebSocket ``agent_ui`` stream.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from nanobot.agent.execution_memory import ExecutionMemoryStore
from nanobot.agent.trajectory import TrajectoryTracer
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import ContextAware, RequestContext
from nanobot.agent.tools.schema import ArraySchema, BooleanSchema, StringSchema, tool_parameters_schema
from nanobot.bus.events import OutboundMessage
from nanobot.session.goal_state import (
    GOAL_STATE_KEY,
    discard_legacy_goal_state_key,
    goal_state_raw,
    goal_state_needs_replan,
    goal_state_ws_blob,
    normalize_goal_list,
    normalize_goal_text,
    parse_goal_state,
)

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


def _iso_now() -> str:
    return datetime.now().isoformat()


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _unique_merge(existing: list[str], incoming: list[str], *, max_items: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        if not item or item in seen:
            continue
        out.append(item)
        seen.add(item)
    if len(out) > max_items:
        return out[-max_items:]
    return out


def _next_pending_step(plan_steps: list[str], completed_steps: list[str]) -> str:
    completed = set(completed_steps)
    for step in plan_steps:
        if step not in completed:
            return step
    return ""


class _GoalToolsMixin(ContextAware):
    """Shared routing context + Session lookup."""

    def __init__(
        self,
        sessions: SessionManager,
        bus: Any | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._request_ctx: RequestContext | None = None
        self._execution_memory = (
            ExecutionMemoryStore(Path(workspace)) if workspace is not None else None
        )
        self._trajectory = (
            TrajectoryTracer(Path(workspace)) if workspace is not None else None
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._request_ctx = ctx

    def _session(self):
        if self._request_ctx is None:
            return None
        key = self._request_ctx.session_key
        if not key:
            return None
        return self._sessions.get_or_create(key)

    async def _publish_goal_state_ws(self, metadata: dict[str, Any]) -> None:
        """Fan-out authoritative goal snapshot for this WebSocket chat only."""
        bus = self._bus
        rc = self._request_ctx
        if bus is None or rc is None or rc.channel != "websocket":
            return
        cid = (rc.chat_id or "").strip()
        if not cid:
            return
        await bus.publish_outbound(
            OutboundMessage(
                channel="websocket",
                chat_id=cid,
                content="",
                metadata={
                    "_goal_state_sync": True,
                    "goal_state": goal_state_ws_blob(metadata),
                },
            ),
        )

    def _append_execution_episode(
        self,
        goal_state: dict[str, Any] | None,
        *,
        event_type: str,
        summary: str,
        step: str | None = None,
        status: str = "info",
        details: str | None = None,
        verified_facts: list[str] | None = None,
    ) -> None:
        if self._execution_memory is None:
            return
        self._execution_memory.append_episode(
            goal_state,
            event_type=event_type,
            summary=summary,
            step=step,
            status=status,
            details=details,
            verified_facts=verified_facts,
        )

    def _append_trajectory_event(
        self,
        goal_state: dict[str, Any] | None,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._trajectory is None:
            return
        chat_id = self._request_ctx.chat_id if self._request_ctx is not None else None
        turn_id = None
        if self._request_ctx is not None:
            turn_id = (
                str(self._request_ctx.metadata.get("turn_id") or self._request_ctx.message_id or "").strip()
                or None
            )
        self._trajectory.append_event(
            goal_state,
            event_type=event_type,
            chat_id=chat_id,
            turn_id=turn_id,
            payload=payload,
        )


@tool_parameters(
    tool_parameters_schema(
        goal=StringSchema(
            "Sustained objective for this chat thread. First read the built-in **long-goal** skill, "
            "especially its Start fast section, then call this promptly once the user's intent is clear. "
            "The goal must still be idempotent, self-contained, bounded, and explicit about done-ness; "
            "do not delay this tool call to over-plan, research, or decide execution details.",
            max_length=12_000,
        ),
        ui_summary=StringSchema(
            "Optional one-line label for session lists / logs (≤120 chars).",
            max_length=120,
            nullable=True,
        ),
        plan_steps=ArraySchema(
            StringSchema("Ordered high-level plan step.", max_length=280),
            description="Optional initial plan steps for this sustained goal.",
            max_items=12,
            nullable=True,
        ),
        current_step=StringSchema(
            "Optional current step to focus on first. If omitted, the first plan step is used.",
            max_length=280,
            nullable=True,
        ),
        progress_summary=StringSchema(
            "Optional short execution summary or starting status note.",
            max_length=1200,
            nullable=True,
        ),
        required=["goal"],
    )
)
class LongTaskTool(Tool, _GoalToolsMixin):
    """Begin or replace focus on a long-running objective stored on the session."""

    def __init__(
        self,
        sessions: Any,
        bus: Any | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        _GoalToolsMixin.__init__(self, sessions, bus, workspace)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sess = getattr(ctx, "sessions", None)
        assert sess is not None  # guarded by enabled()
        return cls(
            sessions=sess,
            bus=getattr(ctx, "bus", None),
            workspace=getattr(ctx, "workspace", None),
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @property
    def name(self) -> str:
        return "long_task"

    @property
    def description(self) -> str:
        return (
            "Mark this thread as a sustained long-running task. "
            "First read the built-in **long-goal** skill, especially its Start fast section; then call this "
            "as soon as the user's intent is clear. Write a good idempotent goal, but do not delay the tool "
            "call with long planning, research, or execution-detail thinking. "
            "The active goal is mirrored in Runtime Context each turn. Use normal tools until done, then call "
            "complete_goal when the objective is satisfied, cancelled, or replaced. "
            "If a goal is already active, finish it or call complete_goal before registering another."
        )

    async def execute(
        self,
        goal: str,
        ui_summary: str | None = None,
        plan_steps: list[str] | None = None,
        current_step: str | None = None,
        progress_summary: str | None = None,
        **kwargs: Any,
    ) -> str:
        sess = self._session()
        if sess is None:
            return (
                "Error: long_task requires an active chat session (missing routing context)."
            )
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if isinstance(prior, dict) and prior.get("status") == "active":
            return (
                "Error: a sustained goal is already active. "
                "Use complete_goal when finished, or ask the user before replacing it."
            )

        summary = normalize_goal_text(ui_summary, max_chars=120)
        normalized_plan = normalize_goal_list(plan_steps, max_items=12, max_chars=280)
        current = normalize_goal_text(
            current_step or (normalized_plan[0] if normalized_plan else ""),
            max_chars=280,
        )
        progress = normalize_goal_text(progress_summary, max_chars=1200)
        blob = {
            "status": "active",
            "objective": goal.strip(),
            "ui_summary": summary,
            "goal_id": uuid4().hex,
            "plan_steps": normalized_plan,
            "current_step": current,
            "completed_steps": [],
            "progress_summary": progress,
            "blocked_reason": "",
            "recent_failures": [],
            "verified_facts": [],
            "replan_count": 0,
            "started_at": _iso_now(),
            "last_updated_at": _iso_now(),
        }
        sess.metadata[GOAL_STATE_KEY] = blob
        discard_legacy_goal_state_key(sess.metadata)
        self._sessions.save(sess)
        self._append_execution_episode(
            blob,
            event_type="goal_started",
            summary=progress or "Sustained goal registered.",
            step=current,
            status="info",
            details=" | ".join(normalized_plan[:4]) if normalized_plan else None,
        )
        self._append_trajectory_event(
            blob,
            event_type="goal_started",
            payload={
                "ui_summary": summary,
                "plan_steps": normalized_plan,
                "current_step": current,
                "progress_summary": progress,
            },
        )
        await self._publish_goal_state_ws(sess.metadata)
        extra = f"\nSummary line: {summary}" if summary else ""
        if current:
            extra += f"\nCurrent step: {current}"
        if normalized_plan:
            extra += f"\nPlan steps: {len(normalized_plan)}"
        return (
            "Goal recorded. Keep working toward the objective using ordinary tools. "
            "When fully done (verified against what was asked), call complete_goal with a "
            f"short recap.{extra}"
        )


@tool_parameters(
    tool_parameters_schema(
        plan_steps=ArraySchema(
            StringSchema("Updated ordered plan step.", max_length=280),
            description="Optional replacement plan for the active goal.",
            max_items=12,
            nullable=True,
        ),
        current_step=StringSchema(
            "Current step to focus on now. Pass an empty string to clear it.",
            max_length=280,
            nullable=True,
        ),
        completed_steps=ArraySchema(
            StringSchema("Step already completed and verified.", max_length=280),
            description="Completed steps to append to the active goal state.",
            max_items=12,
            nullable=True,
        ),
        progress_summary=StringSchema(
            "Updated progress summary for the active goal.",
            max_length=1200,
            nullable=True,
        ),
        blocked_reason=StringSchema(
            "Why the goal is blocked right now. Pass an empty string to clear the blocker.",
            max_length=800,
            nullable=True,
        ),
        failure_note=StringSchema(
            "Short note about a failed attempt that should be remembered and avoided later.",
            max_length=600,
            nullable=True,
        ),
        verified_facts=ArraySchema(
            StringSchema("Verified fact learned during execution.", max_length=240),
            description="Optional verified facts worth recalling in later turns.",
            max_items=8,
            nullable=True,
        ),
        mark_replanned=BooleanSchema(
            description="Set true when the plan changed because of failures or new evidence.",
            default=False,
        ),
        required=[],
    )
)
class UpdateGoalStateTool(Tool, _GoalToolsMixin):
    """Update structured execution state and persist episodic task memory."""

    def __init__(
        self,
        sessions: Any,
        bus: Any | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        _GoalToolsMixin.__init__(self, sessions, bus, workspace)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sess = getattr(ctx, "sessions", None)
        assert sess is not None
        return cls(
            sessions=sess,
            bus=getattr(ctx, "bus", None),
            workspace=getattr(ctx, "workspace", None),
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @property
    def name(self) -> str:
        return "update_goal_state"

    @property
    def description(self) -> str:
        return (
            "Update the active long-running goal with structured execution state: plan steps, current step, "
            "completed steps, progress summary, blockers, failures, and verified facts. Each update also writes "
            "a task-focused episodic memory entry so later turns can retrieve what has already been tried."
        )

    async def execute(
        self,
        plan_steps: list[str] | None = None,
        current_step: str | None = None,
        completed_steps: list[str] | None = None,
        progress_summary: str | None = None,
        blocked_reason: str | None = None,
        failure_note: str | None = None,
        verified_facts: list[str] | None = None,
        mark_replanned: bool = False,
        **kwargs: Any,
    ) -> str:
        sess = self._session()
        if sess is None:
            return "Error: update_goal_state requires an active chat session."
        goal = parse_goal_state(goal_state_raw(sess.metadata))
        if not isinstance(goal, dict) or goal.get("status") != "active":
            return "No active goal to update."

        if (
            plan_steps is None
            and current_step is None
            and completed_steps is None
            and progress_summary is None
            and blocked_reason is None
            and failure_note is None
            and verified_facts is None
            and not mark_replanned
        ):
            return "Error: update_goal_state requires at least one field to change."

        old_plan = normalize_goal_list(goal.get("plan_steps"), max_items=12, max_chars=280)
        updates: list[str] = []

        if plan_steps is not None:
            goal["plan_steps"] = normalize_goal_list(plan_steps, max_items=12, max_chars=280)
            updates.append(f"plan={len(goal['plan_steps'])} steps")

        if completed_steps:
            merged_completed = _unique_merge(
                normalize_goal_list(goal.get("completed_steps"), max_items=12, max_chars=280),
                normalize_goal_list(completed_steps, max_items=12, max_chars=280),
                max_items=12,
            )
            goal["completed_steps"] = merged_completed
            updates.append(f"completed+={len(merged_completed)} total")

        if current_step is not None:
            goal["current_step"] = normalize_goal_text(current_step, max_chars=280)
            updates.append("current_step")

        if progress_summary is not None:
            goal["progress_summary"] = normalize_goal_text(progress_summary, max_chars=1200)
            updates.append("progress_summary")

        if blocked_reason is not None:
            goal["blocked_reason"] = normalize_goal_text(blocked_reason, max_chars=800)
            updates.append("blocked_reason")

        normalized_facts = normalize_goal_list(verified_facts, max_items=8, max_chars=240)
        if normalized_facts:
            goal["verified_facts"] = _unique_merge(
                normalize_goal_list(goal.get("verified_facts"), max_items=8, max_chars=240),
                normalized_facts,
                max_items=8,
            )
            updates.append(f"facts+={len(normalized_facts)}")

        failure = normalize_goal_text(failure_note, max_chars=600)
        if failure:
            goal["recent_failures"] = _unique_merge(
                normalize_goal_list(goal.get("recent_failures"), max_items=3, max_chars=300),
                [failure],
                max_items=3,
            )
            updates.append("failure recorded")

        goal_plan = normalize_goal_list(goal.get("plan_steps"), max_items=12, max_chars=280)
        goal_completed = normalize_goal_list(goal.get("completed_steps"), max_items=12, max_chars=280)
        current = normalize_goal_text(goal.get("current_step"), max_chars=280)
        if (not current or current in set(goal_completed)) and goal_plan:
            goal["current_step"] = _next_pending_step(goal_plan, goal_completed)
        if not goal_plan and goal_completed and current in set(goal_completed):
            goal["current_step"] = ""

        plan_changed = plan_steps is not None and goal_plan != old_plan
        if mark_replanned or plan_changed:
            goal["replan_count"] = _safe_int(goal.get("replan_count")) + 1
            updates.append("replan_count")

        goal["last_updated_at"] = _iso_now()
        sess.metadata[GOAL_STATE_KEY] = goal
        discard_legacy_goal_state_key(sess.metadata)
        self._sessions.save(sess)

        event_type = "failure" if failure else "replan" if mark_replanned or plan_changed else "progress_update"
        status = "failure" if failure else "blocked" if goal.get("blocked_reason") else "info"
        summary = (
            normalize_goal_text(goal.get("progress_summary"), max_chars=240)
            or failure
            or normalize_goal_text(goal.get("blocked_reason"), max_chars=240)
            or "Goal state updated."
        )
        self._append_execution_episode(
            goal,
            event_type=event_type,
            summary=summary,
            step=normalize_goal_text(goal.get("current_step"), max_chars=280),
            status=status,
            details="; ".join(updates),
            verified_facts=normalized_facts,
        )
        self._append_trajectory_event(
            goal,
            event_type="goal_state_updated",
            payload={
                "updates": updates,
                "event_type": event_type,
                "status": status,
                "summary": summary,
            },
        )

        await self._publish_goal_state_ws(sess.metadata)

        response = [
            "Goal state updated.",
            f"Current step: {normalize_goal_text(goal.get('current_step'), max_chars=280) or '(none)'}",
        ]
        if goal_plan:
            response.append(f"Plan progress: {len(goal_completed)}/{len(goal_plan)} steps completed.")
        if goal_state_needs_replan(goal):
            response.append("Replanning is recommended before retrying the same failing path.")
        return "\n".join(response)


@tool_parameters(
    tool_parameters_schema(
        recap=StringSchema(
            "Brief recap for the user (plain text). When the goal succeeded, confirm outcomes; "
            "if the user cancelled, pivoted, or replaced the objective, say so honestly.",
            max_length=8000,
            nullable=True,
        ),
        required=[],
    )
)
class CompleteGoalTool(Tool, _GoalToolsMixin):
    """Mark the active sustained goal finished after all required work is verified."""

    def __init__(
        self,
        sessions: Any,
        bus: Any | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        _GoalToolsMixin.__init__(self, sessions, bus, workspace)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sess = getattr(ctx, "sessions", None)
        assert sess is not None
        return cls(
            sessions=sess,
            bus=getattr(ctx, "bus", None),
            workspace=getattr(ctx, "workspace", None),
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @property
    def name(self) -> str:
        return "complete_goal"

    @property
    def description(self) -> str:
        return (
            "End bookkeeping for the active sustained goal. "
            "Use when the objective is fully achieved and verified—recap what was delivered. "
            "Also call when the user cancels, redirects, or replaces the goal: recap must reflect "
            "what actually happened (not necessarily success). "
            "If no goal is active, the tool reports that and leaves metadata unchanged."
        )

    async def execute(self, recap: str | None = None, **kwargs: Any) -> str:
        sess = self._session()
        if sess is None:
            return "Error: complete_goal requires an active chat session."
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if not isinstance(prior, dict) or prior.get("status") != "active":
            return "No active goal to complete."

        ended = _iso_now()
        sess.metadata[GOAL_STATE_KEY] = {
            **prior,
            "status": "completed",
            "completed_at": ended,
            "recap": (recap or "").strip(),
            "last_updated_at": ended,
        }
        discard_legacy_goal_state_key(sess.metadata)
        self._sessions.save(sess)
        self._append_execution_episode(
            sess.metadata.get(GOAL_STATE_KEY),
            event_type="goal_completed",
            summary=(recap or "").strip() or "Goal completed.",
            status="success",
        )
        self._append_trajectory_event(
            sess.metadata.get(GOAL_STATE_KEY),
            event_type="goal_completed",
            payload={
                "completed_at": ended,
                "recap": (recap or "").strip(),
            },
        )
        await self._publish_goal_state_ws(sess.metadata)
        tail = (recap or "").strip()
        if tail:
            return f"Goal marked complete ({ended}). Recap:\n{tail}"
        return f"Goal marked complete ({ended})."
