"""Rule-based reflection memory for long-horizon execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from nanobot.agent.verifier import VerifierDecision
from nanobot.session.goal_state import normalize_goal_text


@dataclass
class Reflection:
    goal_id: str
    failed_step: str
    failed_action: str
    root_cause: str
    avoid_next_time: str
    suggested_strategy: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_summary(self) -> str:
        return (
            f"Root cause: {self.root_cause} "
            f"Avoid: {self.avoid_next_time} "
            f"Next: {self.suggested_strategy}"
        )


class ReflectionBuilder:
    """Build compact verbal reflections from verifier outputs."""

    def build(
        self,
        goal_state: Mapping[str, Any] | None,
        decision: VerifierDecision,
        *,
        failure_notes: list[str],
    ) -> Reflection | None:
        if not decision.should_reflect and not failure_notes:
            return None
        goal = dict(goal_state or {})
        goal_id = str(goal.get("goal_id") or "").strip()
        if not goal_id:
            return None
        failed_step = normalize_goal_text(goal.get("current_step"), max_chars=280)
        failed_action = normalize_goal_text(failure_notes[0] if failure_notes else "", max_chars=240)
        root_cause = normalize_goal_text(
            decision.root_cause or "The current execution path is not producing progress.",
            max_chars=300,
        )

        if decision.repeated_failure:
            avoid_next_time = "Do not retry the same failing action unless new evidence changes the situation."
        elif decision.need_replan:
            avoid_next_time = "Do not stay on the same blocked step without revising the plan."
        else:
            avoid_next_time = "Use the latest verified evidence before repeating the same action."

        suggested_strategy = normalize_goal_text(
            decision.suggested_next_step or "Inspect the workspace state and choose an alternate path.",
            max_chars=280,
        )
        return Reflection(
            goal_id=goal_id,
            failed_step=failed_step,
            failed_action=failed_action,
            root_cause=root_cause,
            avoid_next_time=avoid_next_time,
            suggested_strategy=suggested_strategy,
            confidence=max(0.0, min(1.0, float(decision.confidence))),
        )
