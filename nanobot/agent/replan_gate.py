"""Verifier-gated auto-replanning requests."""

from __future__ import annotations

from typing import Any, Mapping

from nanobot.agent.reflection import Reflection
from nanobot.agent.verifier import VerifierDecision
from nanobot.session.goal_state import normalize_goal_list, normalize_goal_text


class ReplanGate:
    """Build structured replan injections from verifier and reflection outputs."""

    def __init__(self, *, max_replans_per_goal: int = 3) -> None:
        self.max_replans_per_goal = max(1, int(max_replans_per_goal))

    def build_injection(
        self,
        goal_state: Mapping[str, Any],
        decision: VerifierDecision,
        reflection: Reflection | None,
        *,
        failure_notes: list[str],
    ) -> dict[str, Any]:
        current_step = normalize_goal_text(goal_state.get("current_step"), max_chars=280) or "(unknown)"
        plan_steps = normalize_goal_list(goal_state.get("plan_steps"), max_items=5, max_chars=180)
        replan_count = max(0, int(goal_state.get("replan_count") or 0))

        lines = ["[System-generated execution supervisor request]"]
        if replan_count >= self.max_replans_per_goal:
            lines.extend([
                f"The active goal has already been replanned {replan_count} times.",
                "Do not continue blind auto-replanning.",
                "Report the blocker clearly to the user or ask for clarification before more task tools.",
                f"Current step: {current_step}",
            ])
            if decision.root_cause:
                lines.append(f"Likely blocker: {decision.root_cause}")
            if failure_notes:
                lines.append("Recent failure signals:")
                lines.extend(f"- {note}" for note in failure_notes[:3])
            return {"role": "user", "content": "\n".join(lines)}

        lines.extend([
            "The active long-running goal appears stuck.",
            "Verifier decision:",
            f"- Status: {decision.status}",
            f"- Confidence: {decision.confidence:.2f}",
        ])
        if decision.root_cause:
            lines.append(f"- Root cause: {decision.root_cause}")
        if decision.evidence:
            lines.append("- Evidence:")
            lines.extend(f"  - {item}" for item in decision.evidence[:3])
        if reflection is not None:
            lines.append("Reflection:")
            lines.append(f"- Avoid next time: {reflection.avoid_next_time}")
            lines.append(f"- Suggested strategy: {reflection.suggested_strategy}")
        elif failure_notes:
            lines.append("Recent failure signals:")
            lines.extend(f"- {note}" for note in failure_notes[:3])
        if plan_steps:
            lines.append("Current plan snapshot:")
            lines.extend(f"- {step}" for step in plan_steps[:5])
        lines.extend([
            "Required next action:",
            "Before calling more task tools, call update_goal_state with:",
            "- mark_replanned=true",
            "- a revised current_step",
            "- a progress_summary explaining the changed strategy",
            "- optionally clear blocked_reason if the new plan is actionable",
            "After that, continue with the revised plan instead of repeating the old failing action.",
        ])
        return {"role": "user", "content": "\n".join(lines)}
