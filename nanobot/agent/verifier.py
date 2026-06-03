"""Execution verification for long-horizon goals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from nanobot.session.goal_state import normalize_goal_list, normalize_goal_text
from nanobot.utils.goal_benchmark import normalize_tool_signature


@dataclass
class VerifierDecision:
    status: str
    confidence: float
    progress_made: bool
    step_completed: bool
    need_replan: bool
    repeated_failure: bool
    should_reflect: bool
    root_cause: str
    evidence: list[str]
    suggested_next_step: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuleExecutionVerifier:
    """Rule-based verifier that decides whether the active execution is stuck."""

    def __init__(self, *, stall_turn_limit: int = 2) -> None:
        self.stall_turn_limit = max(1, int(stall_turn_limit))

    def verify(
        self,
        goal_state: Mapping[str, Any] | None,
        *,
        tool_events: list[dict[str, Any]] | None = None,
        trajectory_events: list[dict[str, Any]] | None = None,
    ) -> VerifierDecision:
        goal = dict(goal_state or {})

        current_step = normalize_goal_text(goal.get("current_step"), max_chars=280)
        completed_steps = normalize_goal_list(goal.get("completed_steps"), max_items=50, max_chars=280)
        plan_steps = normalize_goal_list(goal.get("plan_steps"), max_items=50, max_chars=280)
        verified_facts = normalize_goal_list(goal.get("verified_facts"), max_items=20, max_chars=240)
        progress_summary = normalize_goal_text(goal.get("progress_summary"), max_chars=600)
        recent_failures = normalize_goal_list(goal.get("recent_failures"), max_items=8, max_chars=300)
        blocked_reason = normalize_goal_text(goal.get("blocked_reason"), max_chars=400)
        stalled_turn_count = max(0, int(goal.get("_stalled_turn_count") or 0))

        error_events = [event for event in (tool_events or []) if str(event.get("status") or "") == "error"]
        error_name_counts: dict[str, int] = {}
        evidence: list[str] = []
        for event in error_events:
            name = normalize_goal_text(event.get("name"), max_chars=120) or "tool"
            error_name_counts[name] = error_name_counts.get(name, 0) + 1
        repeated_tools = sorted(name for name, count in error_name_counts.items() if count >= 2)
        repeated_signatures = self._repeated_failure_signatures(
            tool_events or [],
            trajectory_events or [],
        )
        if repeated_signatures:
            for signature in repeated_signatures:
                if signature not in repeated_tools:
                    repeated_tools.append(signature)
        if repeated_tools:
            evidence.append("Repeated tool failures: " + ", ".join(repeated_tools))
        if stalled_turn_count >= self.stall_turn_limit and current_step:
            evidence.append(
                f"Current step '{current_step}' stayed unchanged for {stalled_turn_count} turns."
            )
        if blocked_reason:
            evidence.append(f"Blocked reason present: {blocked_reason}")

        if plan_steps and set(completed_steps) >= set(plan_steps):
            return VerifierDecision(
                status="ready_to_complete",
                confidence=0.96,
                progress_made=True,
                step_completed=True,
                need_replan=False,
                repeated_failure=False,
                should_reflect=False,
                root_cause="",
                evidence=[f"All {len(plan_steps)} plan steps are completed."],
                suggested_next_step="Call complete_goal with a final recap.",
            )

        if current_step and current_step in set(completed_steps):
            next_step = next((step for step in plan_steps if step not in set(completed_steps)), "")
            return VerifierDecision(
                status="step_completed",
                confidence=0.88,
                progress_made=True,
                step_completed=True,
                need_replan=False,
                repeated_failure=False,
                should_reflect=False,
                root_cause="",
                evidence=[f"Current step '{current_step}' already appears in completed_steps."],
                suggested_next_step=next_step or "Advance to the next pending step.",
            )

        if repeated_tools:
            repeated_tool_text = ", ".join(repeated_tools)
            return VerifierDecision(
                status="repeated_failure",
                confidence=0.92,
                progress_made=False,
                step_completed=False,
                need_replan=True,
                repeated_failure=True,
                should_reflect=True,
                root_cause=f"The same tool failed repeatedly in the current step: {repeated_tool_text}.",
                evidence=evidence or [f"{repeated_tool_text} failed at least twice in the same turn."],
                suggested_next_step="Revise the strategy before retrying the same failing action.",
            )

        if stalled_turn_count >= self.stall_turn_limit:
            root_cause = (
                f"The current step '{current_step}' appears stuck with no new completed steps or verified facts."
                if current_step
                else "The active goal appears stuck with no progress."
            )
            return VerifierDecision(
                status="stuck",
                confidence=0.86,
                progress_made=False,
                step_completed=False,
                need_replan=True,
                repeated_failure=False,
                should_reflect=True,
                root_cause=root_cause,
                evidence=evidence or ["No new progress signal was observed across repeated turns."],
                suggested_next_step="Choose an alternate action path and update the goal state before continuing.",
            )

        if len(recent_failures) >= 2 and int(goal.get("replan_count") or 0) <= 0:
            return VerifierDecision(
                status="needs_replan",
                confidence=0.8,
                progress_made=False,
                step_completed=False,
                need_replan=True,
                repeated_failure=False,
                should_reflect=True,
                root_cause="Recent failures accumulated without a corresponding replan.",
                evidence=[f"{len(recent_failures)} recent failure notes are stored on the active goal."],
                suggested_next_step="Update the plan to avoid repeating the same failing path.",
            )

        if verified_facts or completed_steps or progress_summary:
            return VerifierDecision(
                status="progress_made",
                confidence=0.72,
                progress_made=True,
                step_completed=False,
                need_replan=False,
                repeated_failure=False,
                should_reflect=False,
                root_cause="",
                evidence=["Progress summary, verified facts, or completed steps indicate forward motion."],
                suggested_next_step=current_step or "Continue with the current plan.",
            )

        return VerifierDecision(
            status="on_track",
            confidence=0.55,
            progress_made=False,
            step_completed=False,
            need_replan=False,
            repeated_failure=False,
            should_reflect=False,
            root_cause="",
            evidence=[],
            suggested_next_step=current_step or "Continue the active step.",
        )

    @staticmethod
    def _event_signature(event: Mapping[str, Any]) -> str:
        name = normalize_goal_text(event.get("name"), max_chars=120) or normalize_goal_text(
            event.get("tool_name"),
            max_chars=120,
        ) or "tool"
        arguments = (
            event.get("arguments")
            or event.get("tool_arguments")
            or event.get("tool_args_preview")
            or {}
        )
        return normalize_tool_signature(name, arguments)

    def _repeated_failure_signatures(
        self,
        tool_events: list[dict[str, Any]],
        trajectory_events: list[dict[str, Any]],
    ) -> list[str]:
        signatures: list[str] = []
        for row in trajectory_events:
            event_type = str(row.get("event_type") or "")
            if event_type == "goal_state_updated":
                payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                if str(payload.get("event_type") or "") == "replan":
                    signatures = []
                continue
            if event_type != "tool_call_failed":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
            signatures.append(self._event_signature(payload))
        for row in tool_events:
            if str(row.get("status") or "") != "error":
                continue
            signatures.append(self._event_signature(row))

        seen: set[str] = set()
        repeated: list[str] = []
        for signature in signatures:
            if signature in seen and signature not in repeated:
                repeated.append(signature)
            seen.add(signature)
        return repeated
