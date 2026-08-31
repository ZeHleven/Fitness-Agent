from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal
from app.models.exercise import Exercise
from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.meal import MealItemCreate, MealLogCreate
from app.schemas.plan_management_proposal import (
    CreatePlanAdjustmentProposalRequest,
    CreatePlanDeletionProposalRequest,
    GenericProposalDecisionRequest,
    GenericProposalDecisionResponse,
    GenericProposalReadResponse,
    PlanCandidate,
    PlanExerciseCandidate,
    PlanProposalReference,
)
from app.schemas.profile import ProfileUpdateRequest
from app.schemas.workout import PersonalizedPlanPreviewRequest
from app.services.agent_intent import ChangeRequest
from app.services.personalized_planner import (
    PersonalizedPlanError,
    is_exercise_compatible,
    preview_personalized_plan,
)
from app.services.plan_management_proposals import PlanProposalError
from app.services.plan_management_proposals import (
    build_plan_snapshot_v2,
    create_manual_plan_adjustment_proposal,
    create_manual_plan_deletion_proposal,
    plan_snapshot_fingerprint,
)
from app.services.profile import calculate_bmi


DOMAIN_PROPOSAL_TYPES = (
    "plan_creation_v1",
    "profile_update_v1",
    "weight_log_create_v1",
    "meal_log_create_v1",
    "meal_log_delete_v1",
)

_PROFILE_FIELDS = {
    "age",
    "gender",
    "height_cm",
    "weight_kg",
    "experience_level",
    "primary_goal",
    "training_days_per_week",
    "session_duration_min",
    "training_location",
    "diet_restriction",
    "injuries",
    "chronic_conditions",
}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_state(profile: UserProfile) -> dict[str, Any]:
    state = {
        field: getattr(profile, field)
        for field in sorted(_PROFILE_FIELDS)
    }
    # SQLAlchemy may retain values assigned before a flush as ``int`` while a
    # later locked reload returns the same database value as ``float``.  The
    # proposal fingerprint must describe the persisted value, not that
    # incidental Python representation.
    for field in ("height_cm", "weight_kg"):
        if state[field] is not None:
            state[field] = float(state[field])
    return state


def _reference(proposal: AgentProposal) -> PlanProposalReference:
    if proposal.expires_at is None or proposal.payload_fingerprint is None:
        raise ValueError("proposal lifecycle metadata is incomplete")
    return PlanProposalReference(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        expires_at=proposal.expires_at,
        payload_fingerprint=proposal.payload_fingerprint,
    )


async def _creation_replay(
    db: AsyncSession,
    *,
    user_id: str,
    request_id: str,
    proposal_type: str,
) -> PlanProposalReference | None:
    existing = await db.scalar(select(AgentProposal).where(
        AgentProposal.user_id == user_id,
        AgentProposal.creation_client_request_id == request_id,
    ))
    if existing is None:
        return None
    if existing.proposal_type != proposal_type:
        raise PlanProposalError(
            "proposal_idempotency_conflict", "该请求标识已用于另一份提案"
        )
    return _reference(existing)


async def _persist(
    db: AsyncSession,
    *,
    user_id: str,
    conversation_id: str,
    run_id: str,
    proposal_type: str,
    request_id: str,
    target_kind: str,
    target_id: str | None,
    payload: dict[str, Any],
    now: datetime,
) -> PlanProposalReference:
    replay = await _creation_replay(
        db,
        user_id=user_id,
        request_id=request_id,
        proposal_type=proposal_type,
    )
    if replay is not None:
        return replay
    proposal = AgentProposal(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type=proposal_type,
        origin="agent_chat",
        creation_client_request_id=request_id,
        target_kind=target_kind,
        target_id=target_id,
        payload_data=payload,
        payload_fingerprint=_fingerprint(payload),
        status="pending_confirmation",
        expires_at=now + timedelta(hours=48),
    )
    db.add(proposal)
    try:
        await db.commit()
        await db.refresh(proposal)
        return _reference(proposal)
    except IntegrityError as exc:
        await db.rollback()
        replay = await _creation_replay(
            db,
            user_id=user_id,
            request_id=request_id,
            proposal_type=proposal_type,
        )
        if replay is not None:
            return replay
        raise PlanProposalError(
            "proposal_creation_conflict",
            "提案创建发生并发冲突，请刷新后重试",
        ) from exc


def _single_value(
    changes: list[ChangeRequest],
    *field_paths: str,
) -> Any:
    matches = [
        change.value for change in changes
        if change.field_path in field_paths
    ]
    if len(matches) != 1 or matches[0] is None:
        raise PlanProposalError(
            "proposal_change_incomplete",
            "写入请求缺少唯一、明确的目标值",
            status_code=422,
        )
    return matches[0]


async def create_agent_profile_update_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "档案与健康修改提案功能暂未开启", status_code=403
        )
    updates: dict[str, Any] = {}
    for change in changes:
        if (
            change.resource not in {"profile", "health"}
            or change.operation != "update"
            or not change.field_path
        ):
            raise PlanProposalError(
                "proposal_change_unsupported", "当前只支持修改已有档案字段", status_code=422
            )
        field = change.field_path.removeprefix("profile.").removeprefix("health.")
        if field not in _PROFILE_FIELDS or change.value is None:
            raise PlanProposalError(
                "proposal_change_incomplete", "档案修改字段或目标值不完整", status_code=422
            )
        if field in updates:
            raise PlanProposalError(
                "proposal_change_ambiguous", f"字段 {field} 出现多个目标值", status_code=422
            )
        updates[field] = change.value
    if not updates:
        raise PlanProposalError("proposal_no_change", "没有可修改的档案字段", status_code=422)
    try:
        validated = ProfileUpdateRequest.model_validate(updates).model_dump(
            exclude_none=True
        )
    except ValidationError as exc:
        raise PlanProposalError(
            "proposal_change_invalid", "档案修改值超出允许范围", status_code=422
        ) from exc
    if set(validated) != set(updates):
        raise PlanProposalError(
            "proposal_change_invalid", "档案修改不能把字段设置为空", status_code=422
        )
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise PlanProposalError("profile_not_found", "个人档案不存在", status_code=404)
    before_all = _profile_state(profile)
    after_all = {**before_all, **validated}
    if all(before_all[key] == value for key, value in validated.items()):
        raise PlanProposalError("proposal_no_change", "档案没有发生变化", status_code=422)
    payload = {
        "schema_version": "1.0.0",
        "proposal_type": "profile_update_v1",
        "target": {
            "resource_type": "user_profile",
            "target_id": profile.id,
            "base_fingerprint": _fingerprint(before_all),
        },
        "before": {key: before_all[key] for key in validated},
        "after": {key: after_all[key] for key in validated},
        "changes": [{
            "field_path": f"profile.{key}",
            "before": before_all[key],
            "after": after_all[key],
        } for key in sorted(validated)],
        "safety_notes": [
            "确认时会重新检查档案版本；健康资料变化后会重新评估活动计划。"
        ],
    }
    return await _persist(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="profile_update_v1",
        request_id=f"agent-proposal:{run_id}:profile_update_v1",
        target_kind="user_profile",
        target_id=profile.id,
        payload=payload,
        now=now or datetime.now(timezone.utc),
    )


async def create_agent_weight_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "体重记录提案功能暂未开启", status_code=403
        )
    if len(changes) != 1 or changes[0].resource != "profile" or changes[0].operation != "create":
        raise PlanProposalError(
            "proposal_change_ambiguous", "一次体重提案只能新增一条明确的体重记录", status_code=422
        )
    value = _single_value(
        changes, "weight_log.weight_kg", "profile.weight_kg", "weight_kg"
    )
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanProposalError(
            "proposal_change_invalid", "体重必须是数字", status_code=422
        ) from exc
    if not 25 <= weight <= 350:
        raise PlanProposalError(
            "proposal_change_invalid", "体重必须在 25–350 kg 之间", status_code=422
        )
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise PlanProposalError("profile_not_found", "个人档案不存在", status_code=404)
    payload = {
        "schema_version": "1.0.0",
        "proposal_type": "weight_log_create_v1",
        "target": {"resource_type": "weight_log", "target_id": None},
        "before": {"current_weight_kg": profile.weight_kg},
        "after": {"weight_kg": weight, "recorded_at": "confirm_time"},
        "changes": [{
            "field_path": "weight_log.weight_kg",
            "before": profile.weight_kg,
            "after": weight,
        }],
        "safety_notes": [],
    }
    return await _persist(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="weight_log_create_v1",
        request_id=f"agent-proposal:{run_id}:weight_log_create_v1",
        target_kind="weight_log",
        target_id=None,
        payload=payload,
        now=now or datetime.now(timezone.utc),
    )


def _parse_logged_at(value: Any) -> date:
    if value in (None, "today", "今天"):
        return date.today()
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise PlanProposalError(
            "proposal_change_invalid", "饮食日期必须是 YYYY-MM-DD", status_code=422
        ) from exc
    if parsed > date.today():
        raise PlanProposalError(
            "proposal_change_invalid", "不能记录未来的饮食", status_code=422
        )
    return parsed


async def _canonical_meal_value(
    db: AsyncSession,
    *,
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanProposalError(
            "proposal_change_incomplete", "请提供餐次、食品和克数", status_code=422
        )
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise PlanProposalError(
            "proposal_change_incomplete", "请至少提供一种食品和克数", status_code=422
        )
    canonical_items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise PlanProposalError(
                "proposal_change_invalid", "食品信息格式不正确", status_code=422
            )
        food_id = raw.get("food_id")
        reference = str(
            raw.get("food_reference") or raw.get("food_name") or ""
        ).strip()
        query = select(Food).where(Food.is_active.is_(True))
        if food_id:
            query = query.where(Food.id == str(food_id))
        elif reference:
            query = query.where(
                func.lower(Food.name_zh) == reference.lower()
            )
        else:
            query = None
        foods = list((await db.execute(query)).scalars().all()) if query is not None else []
        if len(foods) > 1:
            raise PlanProposalError(
                "proposal_target_ambiguous", f"食品“{reference}”匹配到多个结果", status_code=422
            )
        if foods:
            food = foods[0]
            amount = float(raw.get("amount_g") or 0)
            if not 0 < amount <= 10000:
                raise PlanProposalError(
                    "proposal_change_invalid", "食品克数必须在 0–10000 克之间", status_code=422
                )
            factor = amount / 100
            canonical_items.append({
                "food_id": food.id,
                "food_name": food.name_zh,
                "amount_g": amount,
                "calories": round(food.calories_per_100g * factor, 1),
                "protein_g": round(food.protein_g * factor, 1),
                "carbs_g": round(food.carbs_g * factor, 1),
                "fat_g": round(food.fat_g * factor, 1),
            })
            continue
        if food_id or not reference:
            raise PlanProposalError(
                "proposal_target_not_found", f"食品“{reference or food_id}”不存在", status_code=422
            )
        try:
            custom = MealItemCreate.model_validate({
                "food_name": reference,
                "amount_g": raw.get("amount_g"),
                "calories": raw.get("calories"),
                "protein_g": raw.get("protein_g", 0),
                "carbs_g": raw.get("carbs_g", 0),
                "fat_g": raw.get("fat_g", 0),
            })
        except ValidationError as exc:
            raise PlanProposalError(
                "proposal_change_incomplete",
                f"自定义食品“{reference}”需要克数、热量和合法营养值",
                status_code=422,
            ) from exc
        canonical_items.append(custom.model_dump())
    try:
        meal = MealLogCreate.model_validate({
            "logged_at": _parse_logged_at(value.get("logged_at")),
            "meal_type": value.get("meal_type") or "早餐",
            "items": canonical_items,
        })
    except ValidationError as exc:
        raise PlanProposalError(
            "proposal_change_invalid", "餐次或食品数值不符合要求", status_code=422
        ) from exc
    return meal.model_dump(mode="json")


async def create_agent_meal_create_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "饮食记录提案功能暂未开启", status_code=403
        )
    if len(changes) != 1 or changes[0].resource != "nutrition" or changes[0].operation != "create":
        raise PlanProposalError(
            "proposal_change_ambiguous", "一次饮食提案只能新增一条完整餐次", status_code=422
        )
    value = _single_value(changes, "meal", "meal_log", "meal.items")
    after = await _canonical_meal_value(db, value=value)
    payload = {
        "schema_version": "1.0.0",
        "proposal_type": "meal_log_create_v1",
        "target": {"resource_type": "meal_log", "target_id": None},
        "before": None,
        "after": after,
        "changes": [{"field_path": "meal_log", "before": None, "after": after}],
        "safety_notes": ["食品库项目的营养值由服务端按克数计算。"],
    }
    return await _persist(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="meal_log_create_v1",
        request_id=f"agent-proposal:{run_id}:meal_log_create_v1",
        target_kind="meal_log",
        target_id=None,
        payload=payload,
        now=now or datetime.now(timezone.utc),
    )


async def _meal_snapshot(
    db: AsyncSession,
    *,
    user_id: str,
    reference: str,
    lock: bool = False,
) -> dict[str, Any]:
    query = select(MealLog).where(MealLog.user_id == user_id)
    if len(reference) >= 30 and "-" in reference:
        query = query.where(MealLog.id == reference)
    elif reference in {"早餐", "午餐", "晚餐", "加餐"}:
        query = query.where(
            MealLog.logged_at == date.today(), MealLog.meal_type == reference
        )
    else:
        raise PlanProposalError(
            "proposal_target_incomplete", "请提供饮食记录 ID，或明确今天的餐次", status_code=422
        )
    if lock:
        query = query.with_for_update()
    meals = list((await db.execute(query.limit(2))).scalars().all())
    if not meals:
        raise PlanProposalError("proposal_target_not_found", "饮食记录不存在", status_code=404)
    if len(meals) != 1:
        raise PlanProposalError(
            "proposal_target_ambiguous", "该餐次有多条记录，请在饮食页选择具体记录", status_code=422
        )
    meal = meals[0]
    items = list((await db.execute(
        select(MealItem).where(MealItem.meal_id == meal.id).order_by(MealItem.id)
    )).scalars().all())
    return {
        "id": meal.id,
        "logged_at": meal.logged_at.isoformat(),
        "meal_type": meal.meal_type,
        "items": [{
            "id": item.id,
            "food_id": item.food_id,
            "food_name": item.food_name,
            "amount_g": item.amount_g,
            "calories": item.calories,
            "protein_g": item.protein_g,
            "carbs_g": item.carbs_g,
            "fat_g": item.fat_g,
        } for item in items],
    }


async def create_agent_meal_delete_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "饮食删除提案功能暂未开启", status_code=403
        )
    if any(
        change.resource != "nutrition" or change.operation != "delete"
        for change in changes
    ):
        raise PlanProposalError(
            "proposal_change_ambiguous", "一次饮食删除提案只能处理一条餐次", status_code=422
        )
    references = [change.target_reference for change in changes if change.target_reference]
    if len(set(references)) != 1:
        raise PlanProposalError(
            "proposal_target_incomplete", "请明确要删除的具体饮食记录", status_code=422
        )
    before = await _meal_snapshot(
        db, user_id=user_id, reference=references[0]
    )
    payload = {
        "schema_version": "1.0.0",
        "proposal_type": "meal_log_delete_v1",
        "target": {
            "resource_type": "meal_log",
            "target_id": before["id"],
            "base_fingerprint": _fingerprint(before),
        },
        "before": before,
        "after": None,
        "changes": [{"field_path": "meal_log", "before": before, "after": None}],
        "safety_notes": ["确认后将永久删除这条餐次及其全部明细。"],
    }
    return await _persist(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="meal_log_delete_v1",
        request_id=f"agent-proposal:{run_id}:meal_log_delete_v1",
        target_kind="meal_log",
        target_id=before["id"],
        payload=payload,
        now=now or datetime.now(timezone.utc),
    )


async def create_agent_plan_creation_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "Agent 新建计划提案功能暂未开启", status_code=403
        )
    if any(change.resource != "workout_plan" or change.operation != "create" for change in changes):
        raise PlanProposalError(
            "proposal_change_ambiguous", "创建计划请求不能混入其他领域修改", status_code=422
        )
    active = await db.scalar(select(WorkoutPlan.id).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True)
    ).limit(1))
    if active is not None:
        raise PlanProposalError(
            "active_plan_exists", "已有活动计划，请改为调整现有计划", status_code=409
        )
    options: dict[str, Any] = {}
    for change in changes:
        field = change.field_path or ""
        if field == "schedule.days_per_week":
            options["days_per_week"] = change.value
        elif field == "schedule.duration_weeks":
            options["duration_weeks"] = change.value
        elif field in {"plan.goal", "profile.primary_goal"}:
            options["goal"] = change.value
        elif field == "plan" and isinstance(change.value, dict):
            options.update(change.value)
    try:
        request = PersonalizedPlanPreviewRequest.model_validate(options)
        preview = await preview_personalized_plan(
            db, user_id=user_id, request=request
        )
    except (ValidationError, PersonalizedPlanError) as exc:
        raise PlanProposalError(
            "proposal_change_invalid", str(exc), status_code=422
        ) from exc
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise PlanProposalError("profile_not_found", "个人档案不存在", status_code=404)
    # The proposal only needs the selected plan.  The preview's complete
    # exercise-option catalogue is UI helper data and must not be persisted in
    # a durable write proposal.
    after = preview.model_dump(mode="json", exclude={"exercise_options"})
    payload = {
        "schema_version": "1.0.0",
        "proposal_type": "plan_creation_v1",
        "target": {
            "resource_type": "workout_plan",
            "target_id": None,
            "health_context_fingerprint": _fingerprint(_profile_state(profile)),
        },
        "before": None,
        "after": after,
        "changes": [{"field_path": "workout_plan", "before": None, "after": after}],
        "safety_notes": preview.safety_notes,
    }
    return await _persist(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="plan_creation_v1",
        request_id=f"agent-proposal:{run_id}:plan_creation_v1",
        target_kind="workout_plan",
        target_id=None,
        payload=payload,
        now=now or datetime.now(timezone.utc),
    )


def _normalized_name(value: str) -> str:
    return "".join(value.lower().split())


def _unique_planned_target(items: list[dict[str, Any]], reference: str | None) -> dict[str, Any]:
    if not reference:
        raise PlanProposalError(
            "proposal_target_incomplete", "请说明要修改的动作名称", status_code=422
        )
    normalized = _normalized_name(reference)
    exact = [item for item in items if _normalized_name(item["exercise_name"]) == normalized]
    candidates = exact or [
        item for item in items
        if normalized in _normalized_name(item["exercise_name"])
        or _normalized_name(item["exercise_name"]) in normalized
    ]
    if len(candidates) != 1:
        raise PlanProposalError(
            "proposal_target_ambiguous",
            f"当前计划中没有唯一匹配“{reference}”的动作，请使用完整动作名",
            status_code=422,
        )
    return candidates[0]


async def _unique_exercise(
    db: AsyncSession,
    *,
    reference: Any,
) -> Exercise:
    if isinstance(reference, dict):
        exercise_id = reference.get("exercise_id")
        name = reference.get("exercise_name") or reference.get("exercise_reference")
    else:
        exercise_id = None
        name = reference
    query = select(Exercise).where(Exercise.is_active.is_(True))
    if exercise_id:
        query = query.where(Exercise.id == str(exercise_id))
    elif isinstance(name, str) and name.strip():
        query = query.where(func.lower(Exercise.name_zh) == name.strip().lower())
    else:
        raise PlanProposalError(
            "proposal_target_incomplete", "请提供动作库中的动作名称", status_code=422
        )
    matches = list((await db.execute(query.limit(2))).scalars().all())
    if not matches:
        raise PlanProposalError("proposal_target_not_found", "动作不存在或已停用", status_code=422)
    if len(matches) != 1:
        raise PlanProposalError("proposal_target_ambiguous", "动作名称不唯一", status_code=422)
    return matches[0]


async def create_agent_plan_management_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    effect: Literal["update", "delete"],
    changes: list[ChangeRequest],
    now: datetime | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "Agent 计划管理提案功能暂未开启", status_code=403
        )
    if any(change.resource != "workout_plan" for change in changes):
        raise PlanProposalError(
            "proposal_change_ambiguous", "一次提案不能混合多个领域的写入", status_code=422
        )
    active_plans = list((await db.execute(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
        .order_by(WorkoutPlan.created_at.desc())
        .limit(2)
    )).scalars().all())
    if len(active_plans) != 1:
        raise PlanProposalError(
            "active_plan_ambiguous" if active_plans else "active_plan_not_found",
            "当前没有唯一的活动训练计划",
            status_code=409,
        )
    plan = active_plans[0]
    snapshot = await build_plan_snapshot_v2(db, plan=plan)
    fingerprint = plan_snapshot_fingerprint(snapshot)
    moment = now or datetime.now(timezone.utc)
    if effect == "delete":
        return await create_manual_plan_deletion_proposal(
            db,
            enabled=True,
            user_id=user_id,
            plan_id=plan.id,
            request=CreatePlanDeletionProposalRequest(
                client_request_id=f"agent-proposal:{run_id}:plan_deletion_v1",
                expected_base_fingerprint=fingerprint,
            ),
            now=moment,
            origin="agent_chat",
            conversation_id=conversation_id,
            run_id=run_id,
        )

    items = [item.model_dump(mode="python") for item in snapshot.exercises]
    duration_weeks = snapshot.duration_weeks
    training_days = list(snapshot.training_days)
    seen_targets: set[tuple[str, str]] = set()
    for change in changes:
        field = change.field_path
        target_key = _normalized_name(change.target_reference or "")
        semantic_key = (field or change.operation, target_key)
        if semantic_key in seen_targets:
            raise PlanProposalError(
                "proposal_target_ambiguous", "同一个计划目标出现多个变更值", status_code=422
            )
        seen_targets.add(semantic_key)
        if change.operation == "update" and field == "schedule.duration_weeks":
            if isinstance(change.value, bool) or not isinstance(change.value, int):
                raise PlanProposalError("proposal_change_invalid", "计划周期必须是整数", status_code=422)
            duration_weeks = change.value
            continue
        if change.operation == "update" and field == "schedule.days_per_week":
            if isinstance(change.value, bool) or not isinstance(change.value, int):
                raise PlanProposalError("proposal_change_invalid", "每周训练天数必须是整数", status_code=422)
            if change.value != len(training_days) - 1:
                raise PlanProposalError(
                    "proposal_frequency_restructure_unsupported",
                    f"自然语言调整当前仅支持从每周{len(training_days)}天减少为{len(training_days) - 1}天；其他频率请使用计划编辑器精确安排",
                    status_code=422,
                )
            loads = {
                day: sum(int(item["sets"]) for item in items if item["day_of_week"] == day)
                for day in training_days
            }
            removed = min(loads, key=lambda day: (loads[day], -day))
            training_days.remove(removed)
            items = [item for item in items if item["day_of_week"] != removed]
            continue
        if change.operation == "delete" and field in {None, "exercise", "exercise.delete"}:
            target = _unique_planned_target(items, change.target_reference)
            items.remove(target)
            training_days = sorted({int(item["day_of_week"]) for item in items})
            continue
        if change.operation == "create" and field in {"exercise", "exercise.add"}:
            if not isinstance(change.value, dict):
                raise PlanProposalError("proposal_change_incomplete", "新增动作需要动作、训练日和目标值", status_code=422)
            exercise = await _unique_exercise(db, reference=change.value)
            day = change.value.get("day_of_week")
            if day not in training_days:
                raise PlanProposalError("proposal_change_invalid", "新增动作必须放在已有训练日", status_code=422)
            items.append({
                "item_key": f"new:{run_id}:{len(items)}",
                "exercise_id": exercise.id,
                "exercise_name": exercise.name_zh,
                "category": exercise.category,
                "day_of_week": day,
                "sets": change.value.get("sets", 3),
                "reps": str(change.value.get("reps", "8-12")),
                "rest_seconds": change.value.get("rest_seconds", 90),
                "recommended_weight_kg": change.value.get("recommended_weight_kg"),
                "order_index": sum(1 for item in items if item["day_of_week"] == day),
            })
            continue
        target = _unique_planned_target(items, change.target_reference)
        if change.operation == "update" and field == "exercise.exercise_id":
            exercise = await _unique_exercise(db, reference=change.value)
            target.update({
                "exercise_id": exercise.id,
                "exercise_name": exercise.name_zh,
                "category": exercise.category,
            })
            continue
        if change.operation == "update" and field == "exercise.day_of_week":
            if change.value not in training_days:
                raise PlanProposalError("proposal_change_invalid", "目标必须是已有训练日", status_code=422)
            target["day_of_week"] = change.value
            continue
        field_name = {
            "exercise.sets": "sets",
            "exercise.reps": "reps",
            "exercise.rest_seconds": "rest_seconds",
            "exercise.recommended_weight_kg": "recommended_weight_kg",
        }.get(field or "")
        if change.operation != "update" or field_name is None or change.value is None:
            raise PlanProposalError(
                "proposal_change_unsupported", "这项计划变更暂不支持自然语言执行，请使用计划编辑器", status_code=422
            )
        target[field_name] = str(change.value) if field_name == "reps" else change.value

    for day in training_days:
        day_items = [item for item in items if item["day_of_week"] == day]
        if not day_items:
            raise PlanProposalError("proposal_change_invalid", "每个训练日至少需要一个动作", status_code=422)
        for index, item in enumerate(sorted(day_items, key=lambda value: value["order_index"])):
            item["order_index"] = index
    try:
        candidate = PlanCandidate(
            duration_weeks=duration_weeks,
            training_days=training_days,
            exercises=[
                PlanExerciseCandidate.model_validate({
                    key: value
                    for key, value in item.items()
                    if key not in {"exercise_name", "category"}
                })
                for item in items
            ],
        )
    except ValidationError as exc:
        raise PlanProposalError(
            "proposal_change_invalid", "计划变更值超出允许范围", status_code=422
        ) from exc
    return await create_manual_plan_adjustment_proposal(
        db,
        enabled=True,
        user_id=user_id,
        plan_id=plan.id,
        request=CreatePlanAdjustmentProposalRequest(
            client_request_id=f"agent-proposal:{run_id}:plan_adjustment_v2",
            expected_base_fingerprint=fingerprint,
            candidate=candidate,
        ),
        now=moment,
        origin="agent_chat",
        conversation_id=conversation_id,
        run_id=run_id,
    )


def project_domain_proposal_read(
    proposal: AgentProposal,
    *,
    now: datetime,
) -> GenericProposalReadResponse:
    if proposal.payload_fingerprint is None or proposal.expires_at is None:
        raise PlanProposalError("proposal_payload_invalid", "提案生命周期数据不完整")
    status = proposal.status
    if status == "pending_confirmation" and now >= proposal.expires_at:
        status = "expired"
    return GenericProposalReadResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        origin=proposal.origin,
        status=status,
        version=proposal.version,
        payload_fingerprint=proposal.payload_fingerprint,
        payload=proposal.payload_data,
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        allowed_actions=["confirm", "reject"] if status == "pending_confirmation" else [],
        result=proposal.result_data if status == "applied" else None,
    )


async def read_owned_domain_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    now: datetime | None = None,
) -> GenericProposalReadResponse | None:
    proposal = await db.scalar(select(AgentProposal).where(
        AgentProposal.id == proposal_id,
        AgentProposal.user_id == user_id,
        AgentProposal.proposal_type.in_(DOMAIN_PROPOSAL_TYPES),
    ))
    return (
        project_domain_proposal_read(proposal, now=now or datetime.now(timezone.utc))
        if proposal is not None else None
    )


async def _apply_profile_update(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
) -> dict[str, Any]:
    payload = proposal.payload_data
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
    )
    if profile is None or profile.id != payload["target"]["target_id"]:
        raise PlanProposalError("proposal_target_not_found", "个人档案不存在")
    if _fingerprint(_profile_state(profile)) != payload["target"]["base_fingerprint"]:
        raise PlanProposalError("proposal_base_changed", "个人档案已变化，请重新提交修改")
    updates = ProfileUpdateRequest.model_validate(payload["after"]).model_dump(
        exclude_none=True
    )
    for field, value in updates.items():
        setattr(profile, field, value)
    height = updates.get("height_cm", profile.height_cm)
    weight = updates.get("weight_kg", profile.weight_kg)
    if height and weight:
        profile.bmi, profile.bmi_category = calculate_bmi(height, weight)
    await db.flush()
    return {"profile_id": profile.id, "updated_fields": sorted(updates)}


async def _apply_weight_create(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
) -> dict[str, Any]:
    weight = float(proposal.payload_data["after"]["weight_kg"])
    if not 25 <= weight <= 350:
        raise PlanProposalError("proposal_payload_invalid", "体重值不再合法")
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
    )
    if profile is None:
        raise PlanProposalError("profile_not_found", "个人档案不存在")
    log = WeightLog(user_id=user_id, weight_kg=weight)
    db.add(log)
    profile.weight_kg = weight
    if profile.height_cm:
        profile.bmi, profile.bmi_category = calculate_bmi(profile.height_cm, weight)
    await db.flush()
    return {"weight_log_id": log.id, "weight_kg": weight}


async def _apply_meal_create(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
) -> dict[str, Any]:
    canonical = await _canonical_meal_value(db, value=proposal.payload_data["after"])
    if canonical != proposal.payload_data["after"]:
        raise PlanProposalError(
            "proposal_base_changed",
            "食品库营养数据已变化，请重新生成饮食提案",
        )
    meal_data = MealLogCreate.model_validate(canonical)
    meal = MealLog(
        user_id=user_id,
        logged_at=meal_data.logged_at,
        meal_type=meal_data.meal_type,
    )
    db.add(meal)
    await db.flush()
    for item in meal_data.items:
        db.add(MealItem(meal_id=meal.id, **item.model_dump()))
    await db.flush()
    return {
        "meal_log_id": meal.id,
        "logged_at": meal.logged_at.isoformat(),
        "meal_type": meal.meal_type,
    }


async def _apply_meal_delete(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
) -> dict[str, Any]:
    payload = proposal.payload_data
    current = await _meal_snapshot(
        db,
        user_id=user_id,
        reference=payload["target"]["target_id"],
        lock=True,
    )
    if _fingerprint(current) != payload["target"]["base_fingerprint"]:
        raise PlanProposalError("proposal_base_changed", "饮食记录已变化，请重新选择")
    await db.execute(delete(MealItem).where(MealItem.meal_id == current["id"]))
    meal = await db.scalar(select(MealLog).where(MealLog.id == current["id"]))
    if meal is not None:
        await db.delete(meal)
    await db.flush()
    return {"deleted_meal_log_id": current["id"]}


async def _apply_plan_creation(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
) -> dict[str, Any]:
    active = await db.scalar(
        select(WorkoutPlan.id)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
        .with_for_update()
    )
    if active is not None:
        raise PlanProposalError("active_plan_exists", "已有活动计划，不能再新建")
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
    )
    if profile is None or _fingerprint(_profile_state(profile)) != proposal.payload_data["target"]["health_context_fingerprint"]:
        raise PlanProposalError("proposal_health_context_changed", "健康或档案资料已变化，请重新生成计划")
    after = proposal.payload_data["after"]
    exercise_ids = {item["exercise_id"] for item in after["exercises"]}
    exercises = list((await db.execute(
        select(Exercise).where(
            Exercise.id.in_(exercise_ids), Exercise.is_active.is_(True)
        ).with_for_update()
    )).scalars().all())
    if len(exercises) != len(exercise_ids) or any(
        not is_exercise_compatible(profile, exercise) for exercise in exercises
    ):
        raise PlanProposalError("proposal_health_context_changed", "动作已下架或不再符合健康条件")
    plan = WorkoutPlan(
        user_id=user_id,
        name=after["name"],
        goal=after.get("goal"),
        duration_weeks=after["duration_weeks"],
        days_per_week=after["days_per_week"],
        is_active=True,
        ai_generated=True,
        notes=json.dumps({
            "session_duration_min": after.get("session_duration_min"),
            "rationale": after.get("rationale", []),
            "safety_notes": after.get("safety_notes", []),
            "generation_strategy": "agent_profile_rules_v1",
        }, ensure_ascii=False),
    )
    db.add(plan)
    await db.flush()
    for item in after["exercises"]:
        db.add(PlannedExercise(
            plan_id=plan.id,
            exercise_id=item["exercise_id"],
            day_of_week=item["day_of_week"],
            sets=item["sets"],
            reps=item["reps"],
            rest_seconds=item["rest_seconds"],
            recommended_weight_kg=item.get("recommended_weight_kg"),
            order_index=item["order_index"],
        ))
    await db.flush()
    return {"plan_id": plan.id, "plan_name": plan.name}


def _decision_response(proposal: AgentProposal) -> GenericProposalDecisionResponse:
    decided_at = proposal.applied_at if proposal.status == "applied" else proposal.rejected_at
    if decided_at is None or proposal.payload_fingerprint is None:
        raise ValueError("decided proposal metadata is incomplete")
    return GenericProposalDecisionResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        applied=proposal.status == "applied",
        payload_fingerprint=proposal.payload_fingerprint,
        result_data=proposal.result_data,
        decided_at=decided_at,
    )


async def decide_agent_domain_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    action: Literal["confirm", "reject"],
    request: GenericProposalDecisionRequest,
    now: datetime | None = None,
) -> GenericProposalDecisionResponse:
    moment = now or datetime.now(timezone.utc)
    proposal = await db.scalar(
        select(AgentProposal).where(
            AgentProposal.id == proposal_id,
            AgentProposal.user_id == user_id,
            AgentProposal.proposal_type.in_(DOMAIN_PROPOSAL_TYPES),
        ).with_for_update()
    )
    if proposal is None:
        raise PlanProposalError("proposal_not_found", "提案不存在", status_code=404)
    conflict = await db.scalar(select(AgentProposal.id).where(
        AgentProposal.user_id == user_id,
        AgentProposal.decision_client_request_id == request.client_request_id,
        AgentProposal.id != proposal.id,
    ))
    if conflict is not None:
        raise PlanProposalError("proposal_idempotency_conflict", "决策请求标识已被占用")
    expected_status = "applied" if action == "confirm" else "rejected"
    if proposal.status == expected_status and proposal.decision_action == action:
        return _decision_response(proposal)
    if proposal.status != "pending_confirmation":
        raise PlanProposalError("proposal_not_pending", "提案已不能继续决策")
    if proposal.version != request.expected_version:
        raise PlanProposalError("proposal_version_conflict", "提案版本已变化，请刷新")
    if proposal.expires_at is None or moment >= proposal.expires_at:
        proposal.status = "expired"
        proposal.version += 1
        proposal.last_error_code = "proposal_expired"
        await db.commit()
        raise PlanProposalError("proposal_expired", "提案已过期")
    if action == "reject":
        proposal.status = "rejected"
        proposal.version += 1
        proposal.decision_action = "reject"
        proposal.decision_client_request_id = request.client_request_id
        proposal.rejected_at = moment
        await db.commit()
        await db.refresh(proposal)
        return _decision_response(proposal)
    try:
        handlers = {
            "profile_update_v1": _apply_profile_update,
            "weight_log_create_v1": _apply_weight_create,
            "meal_log_create_v1": _apply_meal_create,
            "meal_log_delete_v1": _apply_meal_delete,
            "plan_creation_v1": _apply_plan_creation,
        }
        async with db.begin_nested():
            proposal.result_data = await handlers[proposal.proposal_type](
                db, proposal=proposal, user_id=user_id
            )
            proposal.status = "applied"
            proposal.version += 1
            proposal.decision_action = "confirm"
            proposal.decision_client_request_id = request.client_request_id
            proposal.confirmed_at = moment
            proposal.applied_at = moment
            proposal.last_error_code = None
            await db.flush()
        await db.commit()
        await db.refresh(proposal)
        return _decision_response(proposal)
    except PlanProposalError as exc:
        # ``begin_nested`` has already rolled back the domain write.  Keep the
        # outer transaction alive so unrelated ORM instances are not expired
        # and the stale audit transition can be committed atomically.
        failed = await db.scalar(
            select(AgentProposal).where(AgentProposal.id == proposal_id).with_for_update()
        )
        if failed is not None and failed.status == "pending_confirmation":
            failed.status = "stale"
            failed.version += 1
            failed.decision_action = "confirm"
            failed.decision_client_request_id = request.client_request_id
            failed.confirmed_at = moment
            failed.last_error_code = exc.code
            await db.commit()
        raise
    except Exception as exc:
        # The nested transaction guarantees that a partially applied domain
        # write is rolled back.  Keep a terminal audit record so a retry cannot
        # accidentally execute an uncertain proposal.
        failed = await db.scalar(
            select(AgentProposal).where(
                AgentProposal.id == proposal_id,
                AgentProposal.user_id == user_id,
            ).with_for_update()
        )
        if failed is not None and failed.status == "pending_confirmation":
            failed.status = "failed"
            failed.version += 1
            failed.decision_action = "confirm"
            failed.decision_client_request_id = request.client_request_id
            failed.confirmed_at = moment
            failed.last_error_code = "proposal_execution_failed"
            await db.commit()
        raise PlanProposalError(
            "proposal_execution_failed", "提案执行失败，数据没有被修改"
        ) from exc
