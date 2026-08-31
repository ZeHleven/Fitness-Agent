from __future__ import annotations

from datetime import date

import bcrypt
import pytest
from sqlalchemy import select

from app.models.agent import AgentProposal
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.schemas.plan_management_proposal import (
    CreatePlanAdjustmentProposalRequest,
    CreatePlanDeletionProposalRequest,
    GenericProposalDecisionRequest,
    PlanCandidate,
)
from app.services.plan_management_proposals import (
    build_plan_snapshot_v2,
    create_manual_plan_adjustment_proposal,
    create_manual_plan_deletion_proposal,
    decide_manual_plan_proposal,
    plan_snapshot_fingerprint,
)


async def _seed(db_session, suffix: str):
    user = User(
        id=f"manual-plan-user-{suffix}",
        email=f"manual-plan-{suffix}@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    profile = UserProfile(
        user_id=user.id,
        age=30,
        experience_level="beginner",
        training_location="gym",
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
    )
    squat = Exercise(
        id=f"manual-squat-{suffix}",
        name_zh="高脚杯深蹲",
        name_en=f"Manual Goblet Squat {suffix}",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    row = Exercise(
        id=f"manual-row-{suffix}",
        name_zh="坐姿划船",
        name_en=f"Manual Seated Row {suffix}",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    plan = WorkoutPlan(
        user_id=user.id,
        name="完整训练计划",
        goal="general_fitness",
        duration_weeks=4,
        days_per_week=2,
        is_active=True,
    )
    db_session.add_all([user, profile, squat, row, plan])
    await db_session.flush()
    db_session.add_all([
        PlannedExercise(
            plan_id=plan.id,
            exercise_id=squat.id,
            day_of_week=1,
            sets=3,
            reps="8-12",
            rest_seconds=90,
            order_index=0,
        ),
        PlannedExercise(
            plan_id=plan.id,
            exercise_id=row.id,
            day_of_week=4,
            sets=3,
            reps="8-12",
            rest_seconds=90,
            order_index=0,
        ),
    ])
    await db_session.commit()
    return user, plan


def _decision(proposal_id: str):
    return GenericProposalDecisionRequest(
        expected_version=1,
        client_request_id=f"manual-decision-{proposal_id}",
    )


@pytest.mark.asyncio
async def test_manual_adjustment_is_idempotent_and_preserves_unspecified_plan(db_session):
    user, plan = await _seed(db_session, "adjust")
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    candidate_data = {
        "duration_weeks": 6,
        "training_days": before.training_days,
        "exercises": [{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in before.exercises],
    }
    candidate_data["exercises"][0]["sets"] = 4
    request = CreatePlanAdjustmentProposalRequest(
        client_request_id="manual-adjustment-create-0001",
        expected_base_fingerprint=plan_snapshot_fingerprint(before),
        candidate=PlanCandidate.model_validate(candidate_data),
    )
    first = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=request,
    )
    replay = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=request,
    )
    assert replay.id == first.id

    result = await decide_manual_plan_proposal(
        db_session,
        user_id=user.id,
        proposal_id=first.id,
        action="confirm",
        request=_decision(first.id),
    )
    active = await db_session.scalar(select(WorkoutPlan).where(
        WorkoutPlan.user_id == user.id, WorkoutPlan.is_active.is_(True)
    ))
    after = await build_plan_snapshot_v2(db_session, plan=active)
    await db_session.refresh(plan)
    assert result.status == "applied"
    assert active.id != plan.id
    assert plan.is_active is False
    assert after.duration_weeks == 6
    assert after.exercises[0].sets == 4
    assert after.exercises[1].model_copy(update={"item_key": before.exercises[1].item_key}) == before.exercises[1]


@pytest.mark.asyncio
async def test_new_plan_proposal_supersedes_older_pending_proposal(db_session):
    user, plan = await _seed(db_session, "supersede")
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    base = {
        "duration_weeks": before.duration_weeks,
        "training_days": before.training_days,
        "exercises": [{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in before.exercises],
    }
    first_candidate = PlanCandidate.model_validate({**base, "duration_weeks": 5})
    second_candidate = PlanCandidate.model_validate({**base, "duration_weeks": 6})
    fingerprint = plan_snapshot_fingerprint(before)
    first = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanAdjustmentProposalRequest(
            client_request_id="manual-supersede-create-0001",
            expected_base_fingerprint=fingerprint,
            candidate=first_candidate,
        ),
    )
    second = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanAdjustmentProposalRequest(
            client_request_id="manual-supersede-create-0002",
            expected_base_fingerprint=fingerprint,
            candidate=second_candidate,
        ),
    )
    old = await db_session.get(AgentProposal, first.id)
    assert second.id != first.id
    assert old.status == "stale"
    assert old.last_error_code == "proposal_superseded"


@pytest.mark.asyncio
async def test_confirmed_plan_deletion_detaches_session_and_removes_plan(db_session):
    user, plan = await _seed(db_session, "delete")
    session = WorkoutSession(
        user_id=user.id,
        plan_id=plan.id,
        plan_name=plan.name,
        status="completed",
        trained_at=date.today(),
    )
    db_session.add(session)
    await db_session.commit()
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    proposal = await create_manual_plan_deletion_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanDeletionProposalRequest(
            client_request_id="manual-deletion-create-0001",
            expected_base_fingerprint=plan_snapshot_fingerprint(before),
        ),
    )
    await decide_manual_plan_proposal(
        db_session,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_decision(proposal.id),
    )
    await db_session.refresh(session)
    assert await db_session.get(WorkoutPlan, plan.id) is None
    assert session.plan_id is None
    assert session.plan_name == "完整训练计划"
