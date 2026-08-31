from __future__ import annotations

from datetime import date

import bcrypt
import pytest
from sqlalchemy import select

from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_domain_proposals import (
    create_agent_meal_create_proposal,
    create_agent_profile_update_proposal,
    create_agent_weight_proposal,
    decide_agent_domain_proposal,
    read_owned_domain_proposal,
)
from app.services.agent_intent import ChangeRequest
from app.services.plan_management_proposals import PlanProposalError


async def _context(db_session, suffix: str):
    user = User(
        id=f"domain-user-{suffix}",
        email=f"domain-{suffix}@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    db_session.add(user)
    await db_session.flush()
    profile = UserProfile(
        user_id=user.id,
        age=30,
        height_cm=170,
        weight_kg=65,
        training_days_per_week=3,
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
    )
    conversation = AgentConversation(user_id=user.id)
    db_session.add_all([profile, conversation])
    await db_session.flush()
    run = AgentRun(
        user_id=user.id,
        conversation_id=conversation.id,
        status="completed",
    )
    db_session.add(run)
    await db_session.commit()
    return user, profile, conversation, run


def _decision(run_id: str) -> GenericProposalDecisionRequest:
    return GenericProposalDecisionRequest(
        expected_version=1,
        client_request_id=f"domain-decision-{run_id}",
    )


@pytest.mark.asyncio
async def test_profile_update_is_proposed_then_applied_atomically(db_session):
    user, profile, conversation, run = await _context(db_session, "profile")
    reference = await create_agent_profile_update_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="profile",
            operation="update",
            field_path="profile.training_days_per_week",
            value=4,
        )],
    )
    await db_session.refresh(profile)
    assert profile.training_days_per_week == 3
    read = await read_owned_domain_proposal(
        db_session, user_id=user.id, proposal_id=reference.id
    )
    assert read is not None
    assert read.payload["before"]["training_days_per_week"] == 3
    assert read.payload["after"]["training_days_per_week"] == 4

    result = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    await db_session.refresh(profile)
    assert result.status == "applied"
    assert profile.training_days_per_week == 4

    replay = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    assert replay.result_data == result.result_data


@pytest.mark.asyncio
async def test_weight_proposal_creates_one_log_and_updates_bmi(db_session):
    user, profile, conversation, run = await _context(db_session, "weight")
    reference = await create_agent_weight_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="profile",
            operation="create",
            field_path="weight_log.weight_kg",
            value=67.5,
        )],
    )
    assert await db_session.scalar(select(WeightLog.id).where(WeightLog.user_id == user.id)) is None

    result = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    logs = list((await db_session.execute(
        select(WeightLog).where(WeightLog.user_id == user.id)
    )).scalars().all())
    await db_session.refresh(profile)
    assert result.status == "applied"
    assert [item.weight_kg for item in logs] == [67.5]
    assert profile.weight_kg == 67.5
    assert profile.bmi is not None


@pytest.mark.asyncio
async def test_food_library_meal_proposal_recalculates_server_nutrition(db_session):
    user, _, conversation, run = await _context(db_session, "meal")
    food = Food(
        id="domain-food",
        name_zh="鸡胸肉",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    reference = await create_agent_meal_create_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": date.today().isoformat(),
                "meal_type": "午餐",
                "items": [{
                    "food_id": food.id,
                    "food_name": "伪造名称",
                    "amount_g": 200,
                    "calories": 1,
                    "protein_g": 1,
                    "carbs_g": 1,
                    "fat_g": 1,
                }],
            },
        )],
    )
    assert await db_session.scalar(select(MealLog.id).where(MealLog.user_id == user.id)) is None

    await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    meal = await db_session.scalar(select(MealLog).where(MealLog.user_id == user.id))
    item = await db_session.scalar(select(MealItem).where(MealItem.meal_id == meal.id))
    assert item.food_name == "鸡胸肉"
    assert item.calories == 330
    assert item.protein_g == 62


@pytest.mark.asyncio
async def test_changed_food_data_stales_meal_proposal_without_writing(db_session):
    user, _, conversation, run = await _context(db_session, "meal-stale")
    food = Food(
        id="domain-food-stale",
        name_zh="燕麦",
        category="谷物",
        calories_per_100g=380,
        protein_g=13,
        carbs_g=68,
        fat_g=7,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    reference = await create_agent_meal_create_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": date.today().isoformat(),
                "meal_type": "早餐",
                "items": [{"food_id": food.id, "amount_g": 100}],
            },
        )],
    )
    food.calories_per_100g = 400
    await db_session.commit()

    with pytest.raises(PlanProposalError, match="营养数据已变化"):
        await decide_agent_domain_proposal(
            db_session,
            user_id=user.id,
            proposal_id=reference.id,
            action="confirm",
            request=_decision(run.id),
        )

    assert await db_session.scalar(
        select(MealLog.id).where(MealLog.user_id == user.id)
    ) is None
    proposal = await db_session.scalar(
        select(AgentProposal).where(AgentProposal.id == reference.id)
    )
    assert proposal.status == "stale"
    assert proposal.last_error_code == "proposal_base_changed"
