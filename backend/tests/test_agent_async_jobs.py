import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentMessage, AgentRun
from app.services.agent_jobs import claim_next_agent_run, process_agent_run


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_async_agent_submission_is_idempotent_and_pollable(
    client,
    db_session,
    session_factory,
):
    token = await _token(client, "agent-async@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "我的训练目标是什么？",
        "client_request_id": "async-request-0001",
    }

    first = await client.post("/api/v1/agent/runs", json=payload, headers=headers)
    duplicate = await client.post("/api/v1/agent/runs", json=payload, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["run_id"] == first.json()["run_id"]
    assert duplicate.json()["conversation_id"] == first.json()["conversation_id"]
    run_id = first.json()["run_id"]

    message_count = await db_session.scalar(
        select(func.count(AgentMessage.id)).where(AgentMessage.run_id == run_id)
    )
    assert message_count == 1

    queued = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.json()["reply"] is None

    claimed_run_id = await claim_next_agent_run(db_session)
    assert claimed_run_id == run_id

    mocked_result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "async-tool-1",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": True, "primary_goal": "增肌"}),
                tool_call_id="async-tool-1",
                name="profile_get_summary",
            ),
            AIMessage(content="你当前保存的训练目标是增肌。"),
        ]
    }
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value=mocked_result),
        ),
    ):
        await process_agent_run(session_factory, run_id)

    completed = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["reply"] == "你当前保存的训练目标是增肌。"
    assert completed.json()["cards"][0]["type"] == "profile.get_summary"
    assert completed.json()["poll_after_ms"] is None

    after_completion = await client.post(
        "/api/v1/agent/runs",
        json=payload,
        headers=headers,
    )
    assert after_completion.json()["run_id"] == run_id
    assert after_completion.json()["status"] == "completed"

    conflicting = await client.post(
        "/api/v1/agent/runs",
        json={**payload, "message": "换成另一条消息"},
        headers=headers,
    )
    assert conflicting.status_code == 409


@pytest.mark.asyncio
async def test_stale_running_agent_job_is_reclaimed(client, db_session):
    token = await _token(client, "agent-reclaim@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "查看当前计划",
            "client_request_id": "async-request-reclaim",
        },
        headers=headers,
    )
    run_id = response.json()["run_id"]

    first_claim = await claim_next_agent_run(db_session)
    assert first_claim == run_id
    run = await db_session.get(AgentRun, run_id)
    assert run is not None
    assert run.attempt_count == 1
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    second_claim = await claim_next_agent_run(db_session)
    assert second_claim == run_id
    await db_session.refresh(run)
    assert run.status == "running"
    assert run.attempt_count == 2
    assert run.lease_expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_agent_runs_are_serialized_within_a_conversation(client, db_session):
    token = await _token(client, "agent-serialized@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "先查计划",
            "client_request_id": "serialized-request-1",
        },
        headers=headers,
    )
    conversation_id = first.json()["conversation_id"]
    second = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "再查进度",
            "conversation_id": conversation_id,
            "client_request_id": "serialized-request-2",
        },
        headers=headers,
    )

    first_run_id = await claim_next_agent_run(db_session)
    assert first_run_id == first.json()["run_id"]
    assert await claim_next_agent_run(db_session) is None

    first_run = await db_session.get(AgentRun, first_run_id)
    assert first_run is not None
    first_run.status = "completed"
    first_run.completed_at = datetime.now(timezone.utc)
    first_run.lease_expires_at = None
    await db_session.commit()

    assert await claim_next_agent_run(db_session) == second.json()["run_id"]


@pytest.mark.asyncio
async def test_async_agent_run_is_private_to_owner(client):
    owner_token = await _token(client, "agent-async-owner@example.com")
    other_token = await _token(client, "agent-async-other@example.com")
    created = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "普通健身问题",
            "client_request_id": "async-request-private",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    response = await client.get(
        f"/api/v1/agent/runs/{created.json()['run_id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
