"""Tests for sustained goal tools (`long_task`, `complete_goal`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext
from nanobot.agent.tools.long_task import (
    CompleteGoalTool,
    LongTaskTool,
    UpdateGoalStateTool,
)
from nanobot.bus.queue import MessageBus
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager


def _tools(tmp_path, sm: SessionManager) -> tuple[LongTaskTool, UpdateGoalStateTool, CompleteGoalTool]:
    lt = LongTaskTool(sessions=sm, workspace=tmp_path)
    ug = UpdateGoalStateTool(sessions=sm, workspace=tmp_path)
    cg = CompleteGoalTool(sessions=sm, workspace=tmp_path)
    rc = RequestContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={},
    )
    lt.set_context(rc)
    ug.set_context(rc)
    cg.set_context(rc)
    return lt, ug, cg


@pytest.mark.asyncio
async def test_long_task_records_goal_metadata(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _ug, _cg = _tools(tmp_path, sm)

    out = await lt.execute(
        goal="Do the thing",
        ui_summary="thing",
        plan_steps=["Inspect repo", "Implement fix"],
        progress_summary="Starting execution.",
    )
    assert "Goal recorded" in out

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata.get(GOAL_STATE_KEY)
    assert isinstance(blob, dict)
    assert blob["status"] == "active"
    assert blob["objective"] == "Do the thing"
    assert blob["ui_summary"] == "thing"
    assert blob["goal_id"]
    assert blob["current_step"] == "Inspect repo"
    assert blob["plan_steps"] == ["Inspect repo", "Implement fix"]
    assert blob["progress_summary"] == "Starting execution."


@pytest.mark.asyncio
async def test_long_task_rejects_second_active_goal(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _ug, _cg = _tools(tmp_path, sm)

    await lt.execute(goal="First")
    out = await lt.execute(goal="Second")
    assert "already active" in out


@pytest.mark.asyncio
async def test_complete_goal_closes_active_goal(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _ug, cg = _tools(tmp_path, sm)

    await lt.execute(goal="X")
    out = await cg.execute(recap="Done.")
    assert "marked complete" in out

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata.get(GOAL_STATE_KEY)
    assert blob["status"] == "completed"
    assert blob["recap"] == "Done."


@pytest.mark.asyncio
async def test_long_task_publishes_goal_state_ws_after_save(tmp_path):
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    sm = SessionManager(tmp_path)
    lt = LongTaskTool(sessions=sm, bus=bus, workspace=tmp_path)
    rc = RequestContext(
        channel="websocket",
        chat_id="chat-99",
        session_key="websocket:chat-99",
        metadata={},
    )
    lt.set_context(rc)

    await lt.execute(goal="Objective alpha", ui_summary="alpha")

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args.args[0]
    assert call.channel == "websocket"
    assert call.chat_id == "chat-99"
    assert call.metadata.get("_goal_state_sync") is True
    assert call.metadata["goal_state"]["active"] is True
    assert call.metadata["goal_state"]["ui_summary"] == "alpha"
    assert call.metadata["goal_state"]["objective"] == "Objective alpha"
    assert call.metadata["goal_state"]["goal_id"]


@pytest.mark.asyncio
async def test_complete_goal_publishes_inactive_goal_state_ws(tmp_path):
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    sm = SessionManager(tmp_path)
    lt = LongTaskTool(sessions=sm, bus=bus, workspace=tmp_path)
    cg = CompleteGoalTool(sessions=sm, bus=bus, workspace=tmp_path)
    rc = RequestContext(
        channel="websocket",
        chat_id="chat-z",
        session_key="websocket:chat-z",
        metadata={},
    )
    lt.set_context(rc)
    await lt.execute(goal="X")

    bus.publish_outbound.reset_mock()
    cg.set_context(rc)
    await cg.execute(recap="Done.")

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args.args[0]
    assert call.metadata["goal_state"] == {"active": False}


@pytest.mark.asyncio
async def test_complete_goal_without_active_is_noop_message(tmp_path):
    sm = SessionManager(tmp_path)
    _lt, _ug, cg = _tools(tmp_path, sm)

    out = await cg.execute(recap="n/a")
    assert "No active" in out


@pytest.mark.asyncio
async def test_long_task_skips_ws_publish_without_bus(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _ug, _cg = _tools(tmp_path, sm)
    out = await lt.execute(goal="Solo", ui_summary="s")
    assert "Goal recorded" in out


@pytest.mark.asyncio
async def test_update_goal_state_records_execution_memory(tmp_path):
    sm = SessionManager(tmp_path)
    lt, ug, _cg = _tools(tmp_path, sm)

    await lt.execute(goal="Ship feature", plan_steps=["Plan", "Code", "Test"])
    out = await ug.execute(
        completed_steps=["Plan"],
        progress_summary="Finished planning and started coding.",
        verified_facts=["The failing path is provider timeout handling."],
    )

    assert "Goal state updated." in out

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata[GOAL_STATE_KEY]
    assert blob["completed_steps"] == ["Plan"]
    assert blob["current_step"] == "Code"
    assert blob["verified_facts"] == ["The failing path is provider timeout handling."]

    memory_file = tmp_path / "memory" / "execution_memory.jsonl"
    data = memory_file.read_text(encoding="utf-8")
    assert "Finished planning and started coding." in data
    assert "provider timeout handling" in data


@pytest.mark.asyncio
async def test_update_goal_state_tracks_failures_and_replan_signal(tmp_path):
    sm = SessionManager(tmp_path)
    lt, ug, _cg = _tools(tmp_path, sm)

    await lt.execute(goal="Recover task", plan_steps=["Try A", "Try B"])
    await ug.execute(failure_note="Tool call A timed out.")
    out = await ug.execute(
        failure_note="Retrying A timed out again.",
        blocked_reason="Need a different strategy.",
        mark_replanned=True,
    )

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata[GOAL_STATE_KEY]
    assert len(blob["recent_failures"]) == 2
    assert blob["replan_count"] == 1
    assert "Replanning is recommended" in out


@pytest.mark.asyncio
async def test_long_task_and_goal_tools_registered(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    lt = loop.tools.get("long_task")
    ug = loop.tools.get("update_goal_state")
    cg = loop.tools.get("complete_goal")
    assert lt is not None and lt.name == "long_task"
    assert ug is not None and ug.name == "update_goal_state"
    assert cg is not None and cg.name == "complete_goal"
