"""Task-focused episodic memory for long-horizon execution."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from nanobot.utils.helpers import ensure_dir, truncate_text

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_SUMMARY_MAX_CHARS = 1200
_DETAILS_MAX_CHARS = 2400
_STEP_MAX_CHARS = 280
_FACT_MAX_CHARS = 240
_MAX_FACTS = 8
_MAX_RECALL_ITEMS = 3
_MAX_RUNTIME_LINE = 320


def _iso_now() -> str:
    return datetime.now().isoformat()


def _as_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return truncate_text(text, max_chars) if len(text) > max_chars else text


def _normalize_list(values: Any, *, max_items: int, max_chars: int) -> list[str]:
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
        text = _as_text(item, max_chars=max_chars)
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
        if len(out) >= max_items:
            break
    return out


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        tok = raw.lower()
        if len(tok) > 1 or any("\u4e00" <= ch <= "\u9fff" for ch in tok):
            out.add(tok)
    return out


class ExecutionMemoryStore:
    """Append-only episodic memory for sustained goal execution."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "execution_memory.jsonl"

    def append_episode(
        self,
        goal_state: Mapping[str, Any] | None,
        *,
        event_type: str,
        summary: str,
        step: str | None = None,
        status: str = "info",
        details: str | None = None,
        verified_facts: list[str] | None = None,
    ) -> dict[str, Any] | None:
        goal_id = str((goal_state or {}).get("goal_id") or "").strip()
        if not goal_id:
            return None
        record = {
            "goal_id": goal_id,
            "timestamp": _iso_now(),
            "event_type": _as_text(event_type, max_chars=64) or "progress_update",
            "status": _as_text(status, max_chars=32) or "info",
            "objective": _as_text((goal_state or {}).get("objective"), max_chars=600),
            "step": _as_text(step or (goal_state or {}).get("current_step"), max_chars=_STEP_MAX_CHARS),
            "summary": _as_text(summary, max_chars=_SUMMARY_MAX_CHARS),
            "details": _as_text(details, max_chars=_DETAILS_MAX_CHARS),
            "verified_facts": _normalize_list(
                verified_facts,
                max_items=_MAX_FACTS,
                max_chars=_FACT_MAX_CHARS,
            ),
        }
        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_entries(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    if goal_id and entry.get("goal_id") != goal_id:
                        continue
                    entries.append(entry)
        return entries

    def retrieve(
        self,
        goal_state: Mapping[str, Any] | None,
        query_text: str,
        *,
        limit: int = _MAX_RECALL_ITEMS,
    ) -> list[dict[str, Any]]:
        goal_id = str((goal_state or {}).get("goal_id") or "").strip()
        if not goal_id:
            return []
        entries = self.read_entries(goal_id)
        if not entries:
            return []

        current_step = _as_text((goal_state or {}).get("current_step"), max_chars=_STEP_MAX_CHARS)
        progress = _as_text((goal_state or {}).get("progress_summary"), max_chars=600)
        objective = _as_text((goal_state or {}).get("objective"), max_chars=600)
        query_tokens = _tokens(" ".join(part for part in (objective, current_step, progress, query_text) if part))
        if not query_tokens:
            return entries[-limit:]

        ranked: list[tuple[float, dict[str, Any]]] = []
        total = len(entries)
        step_tokens = _tokens(current_step)
        for idx, entry in enumerate(entries):
            corpus = " ".join([
                str(entry.get("summary") or ""),
                str(entry.get("details") or ""),
                str(entry.get("step") or ""),
                " ".join(str(item) for item in entry.get("verified_facts") or []),
            ])
            entry_tokens = _tokens(corpus)
            overlap = len(query_tokens & entry_tokens)
            recency = (idx + 1) / max(total, 1)
            score = float(overlap * 3) + recency
            if step_tokens and (step_tokens & entry_tokens):
                score += 2.0
            if str(entry.get("status") or "").lower() == "failure":
                score += 1.2
            if not overlap:
                score -= 0.75
            ranked.append((score, entry))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [entry for score, entry in ranked[:limit] if score > 0]

    def build_runtime_recall_lines(
        self,
        goal_state: Mapping[str, Any] | None,
        query_text: str,
        *,
        limit: int = _MAX_RECALL_ITEMS,
    ) -> list[str]:
        entries = self.retrieve(goal_state, query_text, limit=limit)
        if not entries:
            return []
        lines = ["Execution Memory Recall:"]
        for entry in entries:
            label = str(entry.get("status") or entry.get("event_type") or "note").strip()
            summary = _as_text(entry.get("summary"), max_chars=200)
            step = _as_text(entry.get("step"), max_chars=120)
            facts = _normalize_list(entry.get("verified_facts"), max_items=2, max_chars=80)
            parts = [f"- [{label}] {summary or 'Stored execution note.'}"]
            if step:
                parts.append(f"step={step}")
            if facts:
                parts.append(f"facts={'; '.join(facts)}")
            line = " | ".join(parts)
            lines.append(truncate_text(line, _MAX_RUNTIME_LINE))
        return lines
