import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from app.config import settings
from app.models.agent import AgentConversation, AgentRun, AgentToolCall
from app.services.agent_intent import IntentResolution, IntentResolverOutcome


@pytest.fixture(autouse=True)
def disable_intent_model_for_router_tests():
    with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False):
        yield


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_agent_chat_persists_conversation_run_messages_and_tool_audit(
    client,
    db_session,
):
    token = await _token(client, "agent-v1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    mocked_result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "call-profile-1",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": True, "primary_goal": "增肌"}),
                tool_call_id="call-profile-1",
                name="profile_get_summary",
            ),
            AIMessage(content="你当前保存的训练目标是增肌。"),
        ]
    }

    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(return_value=mocked_result),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的训练目标是什么？"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "你当前保存的训练目标是增肌。"
    assert body["cards"][0]["type"] == "profile.get_summary"

    conversations = await client.get(
        "/api/v1/agent/conversations",
        headers=headers,
    )
    assert any(item["id"] == body["conversation_id"] for item in conversations.json())

    messages = await client.get(
        f"/api/v1/agent/conversations/{body['conversation_id']}/messages",
        headers=headers,
    )
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]

    run_response = await client.get(
        f"/api/v1/agent/runs/{body['run_id']}",
        headers=headers,
    )
    assert run_response.status_code == 200
    assert run_response.json()["tool_allowlist"] == ["profile.get_summary"]
    assert run_response.json()["intent_source"] == "rules"
    assert run_response.json()["intent_confidence"] == 0.9
    assert run_response.json()["duration_ms"] is not None

    tool_call = await db_session.scalar(
        select(AgentToolCall).where(AgentToolCall.run_id == body["run_id"])
    )
    assert tool_call is not None
    assert tool_call.tool_name == "profile.get_summary"
    assert "primary_goal" in tool_call.result_data["fields_returned"]
    assert "增肌" not in str(tool_call.result_data)


@pytest.mark.asyncio
async def test_agent_conversation_is_isolated_by_user(client):
    first_token = await _token(client, "agent-owner@example.com")
    second_token = await _token(client, "agent-other@example.com")
    result = {"messages": [AIMessage(content="回答")]}
    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(return_value=result),
    ):
        created = await client.post(
            "/api/v1/agent/chat",
            json={"message": "普通健身问题"},
            headers={"Authorization": f"Bearer {first_token}"},
        )

    response = await client.get(
        f"/api/v1/agent/conversations/{created.json()['conversation_id']}/messages",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_agent_unconfigured_model_returns_503_and_marks_run_failed(
    client,
    db_session,
):
    token = await _token(client, "agent-disabled@example.com")
    with patch.object(settings, "DEEPSEEK_API_KEY", ""):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的当前计划是什么？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    failed_run = await db_session.scalar(
        select(AgentRun)
        .where(AgentRun.model_name == settings.AGENT_MODEL)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.error_code == "ai_service_error"


@pytest.mark.asyncio
async def test_agent_health_red_flag_is_intercepted_before_model(client):
    token = await _token(client, "agent-red-flag@example.com")
    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(),
    ) as invoke_agent:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我训练时突然胸痛，还能继续吗？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "停止训练" in response.json()["reply"]
    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.json()["risk_level"] == "high"
    assert run.json()["tool_allowlist"] == []
    invoke_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_required_clarification_does_not_call_tools_or_main_model(client):
    token = await _token(client, "agent-clarification@example.com")
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="workout_history_query",
            missing_slots=["要查询的时间范围"],
            clarification_required=True,
            confidence=0.62,
        ),
        source="model",
        attempt_count=1,
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as invoke_agent,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "帮我比较一下以前的训练"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "时间范围" in response.json()["reply"]
    invoke_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_affirmative_followup_routes_from_conversation_context(client):
    token = await _token(client, "agent-followup@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    invoke_agent = AsyncMock(side_effect=[
        {"messages": [AIMessage(content="需要我帮你查下次该练什么吗？")]},
        {"messages": [AIMessage(content="你的下一练已经查到了。")]},
    ])
    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=invoke_agent,
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我不知道接下来怎么安排"},
            headers=headers,
        )
        second = await client.post(
            "/api/v1/agent/chat",
            json={
                "message": "需要",
                "conversation_id": first.json()["conversation_id"],
            },
            headers=headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    second_run = await client.get(
        f"/api/v1/agent/runs/{second.json()['run_id']}",
        headers=headers,
    )
    assert second_run.json()["primary_intent"] == "next_workout_query"
    assert second_run.json()["tool_allowlist"] == ["workout.get_next"]
    assert second_run.json()["intent_fallback_reason"] == "contextual_followup"


@pytest.mark.asyncio
async def test_agent_persists_clarification_and_resumes_after_slot_fill(
    client,
    db_session,
):
    token = await _token(client, "agent-slot-fill@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    clarification = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="workout_history_query",
            resolved_query="比较我的训练历史",
            subtasks=["读取训练历史", "比较训练表现"],
            missing_slots=["要比较的时间范围"],
            clarification_required=True,
            clarification_question="你想比较哪个时间范围？",
            confidence=0.72,
        ),
        source="model",
        attempt_count=1,
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=clarification),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as first_invoke,
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            json={"message": "帮我比较训练表现"},
            headers=headers,
        )

    assert first.status_code == 200
    assert first.json()["reply"] == "你想比较哪个时间范围？"
    first_invoke.assert_not_awaited()
    conversation = await db_session.get(
        AgentConversation,
        first.json()["conversation_id"],
    )
    assert conversation is not None
    assert conversation.pending_clarification["missing_slots"] == [
        "要比较的时间范围"
    ]

    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(return_value={
            "messages": [AIMessage(content="这是你最近四周的训练比较。")],
        }),
    ) as second_invoke:
        second = await client.post(
            "/api/v1/agent/chat",
            json={
                "message": "最近四周",
                "conversation_id": first.json()["conversation_id"],
            },
            headers=headers,
        )

    assert second.status_code == 200
    second_run = await client.get(
        f"/api/v1/agent/runs/{second.json()['run_id']}",
        headers=headers,
    )
    run_body = second_run.json()
    assert run_body["intent_fallback_reason"] == "clarification_filled"
    assert run_body["tool_allowlist"] == ["workout.list_history"]
    assert run_body["missing_slots"] == []
    assert "最近四周" in run_body["resolved_query"]
    second_invoke.assert_awaited_once()
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
