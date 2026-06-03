"""Tests for automatic failure-aware replanning in the main agent loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.session.goal_state import GOAL_STATE_KEY


@pytest.mark.asyncio
async def test_run_agent_loop_injects_auto_replan_after_repeated_failures(tmp_path):
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    captured_messages: list[list[dict[str, object]]] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        captured_messages.append([dict(message) for message in messages])
        if call_count["n"] == 1:
            return LLMResponse(
                content="Trying the current path.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                    ToolCallRequest(
                        id="tc2",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                ],
                usage={},
            )
        if call_count["n"] == 2:
            assert any(
                message.get("role") == "user"
                and "[System-generated replanning request]" in str(message.get("content") or "")
                for message in messages
            )
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc3",
                        name="update_goal_state",
                        arguments={
                            "plan_steps": ["Find alternate source", "Download PDF", "Summarize method"],
                            "current_step": "Find alternate source",
                            "progress_summary": "Switching strategy after repeated read failures.",
                            "mark_replanned": True,
                        },
                    )
                ],
                usage={},
            )
        return LLMResponse(content="Replanned successfully.", usage={})

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    original_execute = loop.tools.execute

    async def execute_proxy(name, params):
        if name == "read_file":
            return "Error: mirror returned 403"
        return await original_execute(name, params)

    loop.tools.execute = AsyncMock(side_effect=execute_proxy)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("websocket:chat-auto-replan")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "goal_id": "goal-auto",
        "objective": "Download the paper PDF and summarize it.",
        "ui_summary": "paper task",
        "plan_steps": ["Locate source", "Download PDF", "Summarize method"],
        "current_step": "Download PDF",
        "completed_steps": ["Locate source"],
        "progress_summary": "Trying the first PDF source.",
        "blocked_reason": "",
        "recent_failures": [],
        "verified_facts": [],
        "replan_count": 0,
        "started_at": "2026-05-22T11:00:00",
        "last_updated_at": "2026-05-22T11:00:00",
    }
    loop.sessions.save(session)

    final_content, _, messages, stop_reason, had_injections = await loop._run_agent_loop(
        [{"role": "user", "content": "please keep going"}],
        session=session,
        session_key=session.key,
        channel="websocket",
        chat_id="chat-auto-replan",
        metadata={},
    )

    assert final_content == "Replanned successfully."
    assert stop_reason == "completed"
    assert had_injections is True
    assert any(
        message.get("role") == "user"
        and "[System-generated replanning request]" in str(message.get("content") or "")
        for message in messages
    )

    goal = session.metadata[GOAL_STATE_KEY]
    assert goal["current_step"] == "Find alternate source"
    assert goal["replan_count"] == 1
    assert goal["blocked_reason"]
    assert goal["recent_failures"]

    memory_text = (tmp_path / "memory" / "execution_memory.jsonl").read_text(encoding="utf-8")
    assert "Repeated failures on current step 'Download PDF'" in memory_text
    assert "Switching strategy after repeated read failures." in memory_text


@pytest.mark.asyncio
async def test_run_agent_loop_skips_auto_replan_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOBOT_ENABLE_AUTO_REPLAN", "0")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="Trying the current path.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                    ToolCallRequest(
                        id="tc2",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                ],
                usage={},
            )
        return LLMResponse(content="Still stuck.", usage={})

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    loop.tools.execute = AsyncMock(return_value="Error: mirror returned 403")  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("websocket:chat-auto-replan")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "goal_id": "goal-auto",
        "objective": "Download the paper PDF and summarize it.",
        "current_step": "Download PDF",
        "plan_steps": ["Locate source", "Download PDF"],
        "completed_steps": [],
        "recent_failures": [],
        "verified_facts": [],
        "replan_count": 0,
    }
    loop.sessions.save(session)

    final_content, _, messages, stop_reason, had_injections = await loop._run_agent_loop(
        [{"role": "user", "content": "please keep going"}],
        session=session,
        session_key=session.key,
        channel="websocket",
        chat_id="chat-auto-replan",
        metadata={},
    )

    assert final_content == "Still stuck."
    assert stop_reason == "completed"
    assert had_injections is False
    assert not any(
        message.get("role") == "user"
        and "[System-generated replanning request]" in str(message.get("content") or "")
        for message in messages
    )


@pytest.mark.asyncio
async def test_run_agent_loop_supervisor_creates_reflection_and_trajectory(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NANOBOT_ENABLE_EXECUTION_VERIFIER", "1")
    monkeypatch.setenv("NANOBOT_ENABLE_REFLECTION_MEMORY", "1")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="Trying the current path.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                    ToolCallRequest(
                        id="tc2",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                ],
                usage={},
            )
        if call_count["n"] == 2:
            assert any(
                message.get("role") == "user"
                and "[System-generated execution supervisor request]" in str(message.get("content") or "")
                for message in messages
            )
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc3",
                        name="update_goal_state",
                        arguments={
                            "plan_steps": ["Find alternate source", "Download PDF", "Summarize method"],
                            "current_step": "Find alternate source",
                            "progress_summary": "Switching strategy after supervisor reflection.",
                            "mark_replanned": True,
                        },
                    )
                ],
                usage={},
            )
        return LLMResponse(content="Recovered with a revised plan.", usage={})

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    original_execute = loop.tools.execute

    async def execute_proxy(name, params):
        if name == "read_file":
            return "Error: mirror returned 403"
        return await original_execute(name, params)

    loop.tools.execute = AsyncMock(side_effect=execute_proxy)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("websocket:chat-supervisor")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "goal_id": "goal-supervisor",
        "objective": "Download the paper PDF and summarize it.",
        "ui_summary": "paper task",
        "plan_steps": ["Locate source", "Download PDF", "Summarize method"],
        "current_step": "Download PDF",
        "completed_steps": ["Locate source"],
        "progress_summary": "Trying the first PDF source.",
        "blocked_reason": "",
        "recent_failures": [],
        "verified_facts": [],
        "replan_count": 0,
        "started_at": "2026-05-22T11:00:00",
        "last_updated_at": "2026-05-22T11:00:00",
    }
    loop.sessions.save(session)

    final_content, _, messages, stop_reason, had_injections = await loop._run_agent_loop(
        [{"role": "user", "content": "please keep going"}],
        session=session,
        session_key=session.key,
        channel="websocket",
        chat_id="chat-supervisor",
        metadata={},
    )

    assert final_content == "Recovered with a revised plan."
    assert stop_reason == "completed"
    assert had_injections is True
    assert any(
        message.get("role") == "user"
        and "[System-generated execution supervisor request]" in str(message.get("content") or "")
        for message in messages
    )

    memory_text = (tmp_path / "memory" / "execution_memory.jsonl").read_text(encoding="utf-8")
    assert "Root cause:" in memory_text
    assert "Switching strategy after supervisor reflection." in memory_text

    trace_text = (tmp_path / "traces" / "trajectory_goal-supervisor.jsonl").read_text(encoding="utf-8")
    assert "tool_call_failed" in trace_text
    assert "verifier_decision" in trace_text
    assert "reflection_created" in trace_text
    assert "replan_requested" in trace_text


@pytest.mark.asyncio
async def test_run_agent_loop_supervisor_detects_repeated_failures_across_tool_rounds(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NANOBOT_ENABLE_EXECUTION_VERIFIER", "1")
    monkeypatch.setenv("NANOBOT_ENABLE_REFLECTION_MEMORY", "1")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="Try reading the file.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                ],
                usage={},
            )
        if call_count["n"] == 2:
            return LLMResponse(
                content="Try the same file again.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc2",
                        name="read_file",
                        arguments={"path": "missing.txt"},
                    ),
                ],
                usage={},
            )
        if call_count["n"] == 3:
            assert any(
                message.get("role") == "user"
                and "[System-generated execution supervisor request]" in str(message.get("content") or "")
                for message in messages
            )
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc3",
                        name="update_goal_state",
                        arguments={
                            "plan_steps": ["Find alternate source", "Download PDF"],
                            "current_step": "Find alternate source",
                            "progress_summary": "Switching after repeated failures across tool rounds.",
                            "mark_replanned": True,
                        },
                    )
                ],
                usage={},
            )
        return LLMResponse(content="Recovered after supervisor intervention.", usage={})

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    loop.tools.execute = AsyncMock(return_value="Error: mirror returned 403")  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("websocket:chat-supervisor-history")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "goal_id": "goal-supervisor-history",
        "objective": "Download the paper PDF and summarize it.",
        "current_step": "Download PDF",
        "plan_steps": ["Locate source", "Download PDF"],
        "completed_steps": [],
        "recent_failures": [],
        "verified_facts": [],
        "replan_count": 0,
    }
    loop.sessions.save(session)

    final_content, _, messages, stop_reason, had_injections = await loop._run_agent_loop(
        [{"role": "user", "content": "keep going"}],
        session=session,
        session_key=session.key,
        channel="websocket",
        chat_id="chat-supervisor-history",
        metadata={},
    )

    assert final_content == "Recovered after supervisor intervention."
    assert stop_reason == "completed"
    assert had_injections is True
    assert any(
        message.get("role") == "user"
        and "[System-generated execution supervisor request]" in str(message.get("content") or "")
        for message in messages
    )

    trace_text = (tmp_path / "traces" / "trajectory_goal-supervisor-history.jsonl").read_text(encoding="utf-8")
    assert "reflection_created" in trace_text
    assert "replan_requested" in trace_text
