from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentMessage, AgentProposal, AgentRun
from app.models.user import User
from app.schemas.agent_trace import (
    AgentExecutionTrace,
    AgentFinalizationContractTrace,
    AgentPlanTrace,
)
from app.services.agent_controller import PlannedExecutionResult
from app.services.agent_plan_adjustment_proposal_persistence import (
    persist_optional_plan_adjustment_proposal,
)


_USER_MESSAGE = (
    "最近四周完成率很低，当前训练计划是否太激进，请给调整建议"
)


@pytest.fixture(autouse=True)
def disable_intent_model_for_proposal_runtime_tests():
    with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False):
        yield


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


async def _seed_active_plan(
    client,
    *,
    email: str,
    suffix: str,
) -> tuple[str, dict]:
    token = await _token(client, email)
    return token, {
        "id": f"proposal-runtime-plan-{suffix}",
        "name": "基础力量计划",
        "goal": "strength",
        "duration_weeks": 4,
        "days_per_week": 1,
        "exercises": [{
            "exercise_id": f"proposal-runtime-exercise-{suffix}",
            "exercise_name": "高脚杯深蹲",
            "day_of_week": 1,
            "sets": 4,
            "reps": "8-10",
            "rest_seconds": 120,
            "recommended_weight_kg": None,
            "order_index": 0,
        }],
    }


def _target_reduction_draft() -> dict:
    return {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": 4},
            "after": {"sets": 3},
            "reason": "近期完成率偏低，先保守降低单次训练量。",
            "safety_priority": False,
        }],
        "rationale": ["先降低训练量以提高连续完成概率。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }


def _planned_result(
    plan: dict,
    *,
    proposal_draft: dict | None,
) -> PlannedExecutionResult:
    trace = AgentExecutionTrace(
        execution_mode="planned",
        risk_level="low",
        status="completed",
        plan=AgentPlanTrace(
            goal="根据当前计划和四周进度评估调整",
            planner_source="model_micro_plan_v1",
            candidate_tools=[
                "plan.get_active",
                "workout.get_progress",
                "workout.list_history",
            ],
        ),
        finalization_contract=AgentFinalizationContractTrace(
            allowed_outcomes=[
                "adjustment_proposal",
                "no_change_needed",
                "insufficient_evidence",
            ],
            selected_outcome="adjustment_proposal",
            derived_terminal_action="proposal",
        ),
        terminal_action="proposal",
        termination_reason="agent_completed",
    )
    observations = [
        {
            "step_id": "step_1",
            "call_id": "fixture-plan",
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {"found": True, "plan": plan},
        },
        {
            "step_id": "step_1",
            "call_id": "fixture-progress",
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {
                "weeks": 4,
                "total_sessions": 0,
                "total_sets": 0,
                "total_reps": 0,
                "total_volume_kg": 0,
                "weekly": [],
            },
        },
    ]
    return PlannedExecutionResult(
        reply="建议先减少一组；提案尚未执行，需要你确认。",
        execution_trace=trace,
        cards=[{"type": "plan.get_active", "data": {"found": True}}],
        proposal_draft=proposal_draft,
        proposal_observations=observations,
    )


@pytest.mark.asyncio
async def test_flag_off_preserves_exact_legacy_response_and_skips_proposal_path(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-off@example.com",
        suffix="off",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            False,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.build_runtime_plan_adjustment_proposal"
        ) as build_proposal,
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=AsyncMock(),
        ) as persist_proposal,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    assert set(response.json()) == {
        "reply",
        "conversation_id",
        "run_id",
        "cards",
    }
    assert "proposal" not in response.json()
    build_proposal.assert_not_called()
    persist_proposal.assert_not_awaited()
    assert execute_planned.await_args.kwargs[
        "proposal_creation_enabled"
    ] is False
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == response.json()["run_id"]
        )
    )
    assert proposal_count == 0

    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    assert "proposal" not in run_response.json()


@pytest.mark.asyncio
async def test_flag_on_atomically_persists_and_returns_minimal_proposal_reference(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-on@example.com",
        suffix="on",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    proposal_reference = response.json()["proposal"]
    assert set(proposal_reference) == {
        "id",
        "proposal_type",
        "status",
        "version",
        "expires_at",
        "payload_fingerprint",
    }
    assert proposal_reference["proposal_type"] == "plan_adjustment_v1"
    assert proposal_reference["status"] == "pending_confirmation"
    assert proposal_reference["version"] == 1
    assert execute_planned.await_args.kwargs[
        "proposal_creation_enabled"
    ] is True

    proposal = await db_session.get(
        AgentProposal,
        proposal_reference["id"],
    )
    assert proposal is not None
    assert proposal.run_id == response.json()["run_id"]
    assert proposal.base_plan_id == plan["id"]
    assert proposal.payload_data["before"]["exercises"][0]["sets"] == 4
    assert proposal.payload_data["after"]["exercises"][0]["sets"] == 3
    assert proposal.payload_data["target"]["base_plan_id"] == plan["id"]
    assert {item["tool_id"] for item in proposal.payload_data["evidence"]} == {
        "plan.get_active",
        "workout.get_progress",
    }
    assert "user_id" not in proposal.payload_data

    assistant = await db_session.scalar(
        select(AgentMessage).where(
            AgentMessage.run_id == response.json()["run_id"],
            AgentMessage.role == "assistant",
        )
    )
    assert assistant is not None
    assert assistant.content_data["proposal"] == proposal_reference

    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    assert run_response.json()["proposal"] == proposal_reference


@pytest.mark.asyncio
async def test_flag_on_invalid_draft_keeps_text_response_without_proposal(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-invalid@example.com",
        suffix="invalid",
    )
    invalid_draft = _target_reduction_draft()
    invalid_draft["changes"][0]["stable_display_key"] = "unknown-slot"
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=invalid_draft,
    ))
    persist_proposal = AsyncMock()

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=persist_proposal,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    assert "proposal" not in response.json()
    assert "尚未执行" in response.json()["reply"]
    persist_proposal.assert_not_awaited()
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == response.json()["run_id"]
        )
    )
    assert proposal_count == 0


@pytest.mark.asyncio
async def test_persistence_error_after_flush_rolls_back_proposal_and_answer(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-rollback@example.com",
        suffix="rollback",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    async def persist_then_fail(*args, **kwargs):
        await persist_optional_plan_adjustment_proposal(*args, **kwargs)
        raise RuntimeError("fixture persistence failure after flush")

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=persist_then_fail,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 503
    user = await db_session.scalar(
        select(User).where(
            User.email == "proposal-runtime-rollback@example.com"
        )
    )
    assert user is not None
    run = await db_session.scalar(
        select(AgentRun)
        .where(AgentRun.user_id == user.id)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "agent_runtime_error"
    assistant_count = await db_session.scalar(
        select(func.count(AgentMessage.id)).where(
            AgentMessage.run_id == run.id,
            AgentMessage.role == "assistant",
        )
    )
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == run.id
        )
    )
    assert assistant_count == 0
    assert proposal_count == 0
