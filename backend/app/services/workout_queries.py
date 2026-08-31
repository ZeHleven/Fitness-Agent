from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, SessionExercise, WorkoutPlan, WorkoutSession
from app.schemas.workout import (
    PlannedExerciseResponse,
    SessionExerciseResponse,
    WeeklyWorkoutProgress,
    WorkoutAdjustmentResponse,
    WorkoutFeedback,
    WorkoutPlanDetail,
    WorkoutProgressResponse,
    WorkoutSessionDetail,
)


def sets_metrics(sets_data: object) -> tuple[int, int, float]:
    """Return valid set count, repetitions and external-load volume."""
    if not isinstance(sets_data, list):
        return 0, 0, 0
    reps = 0
    volume = 0.0
    valid_sets = 0
    for item in sets_data:
        if not isinstance(item, dict):
            continue
        item_reps = item.get("reps")
        if not isinstance(item_reps, int) or item_reps < 1:
            continue
        weight = item.get("weight_kg")
        item_weight = float(weight) if isinstance(weight, (int, float)) else 0.0
        valid_sets += 1
        reps += item_reps
        volume += item_reps * item_weight
    return valid_sets, reps, round(volume, 1)


def normalized_sets(sets_data: object) -> list[dict]:
    if not isinstance(sets_data, list):
        return []
    return [dict(item) for item in sets_data if isinstance(item, dict)]


def best_performance(sets_data: list[dict]) -> tuple[float | None, int | None]:
    weighted: list[tuple[float, int]] = []
    bodyweight_reps: list[int] = []
    for item in sets_data:
        reps = item.get("reps")
        if not isinstance(reps, int) or reps < 1:
            continue
        weight = item.get("weight_kg")
        if isinstance(weight, (int, float)) and float(weight) > 0:
            weighted.append((float(weight), reps))
        else:
            bodyweight_reps.append(reps)
    if weighted:
        best_weight, best_reps = max(weighted, key=lambda value: (value[0], value[1]))
        return best_weight, best_reps
    if bodyweight_reps:
        return None, max(bodyweight_reps)
    return None, None


def is_personal_record(candidate: dict, baseline: list[dict]) -> bool:
    reps = candidate.get("reps")
    if not isinstance(reps, int) or reps < 1:
        return False
    weight = candidate.get("weight_kg")
    if isinstance(weight, (int, float)) and float(weight) > 0:
        prior_weighted = [
            (float(item["weight_kg"]), item["reps"])
            for item in baseline
            if isinstance(item.get("reps"), int)
            and item["reps"] > 0
            and isinstance(item.get("weight_kg"), (int, float))
            and float(item["weight_kg"]) > 0
        ]
        return not prior_weighted or (float(weight), reps) > max(prior_weighted)
    prior_bodyweight_reps = [
        item["reps"]
        for item in baseline
        if isinstance(item.get("reps"), int)
        and item["reps"] > 0
        and not (
            isinstance(item.get("weight_kg"), (int, float))
            and float(item["weight_kg"]) > 0
        )
    ]
    return not prior_bodyweight_reps or reps > max(prior_bodyweight_reps)


async def list_user_plans(db: AsyncSession, *, user_id: str) -> list[WorkoutPlan]:
    return list((await db.execute(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id)
        .order_by(WorkoutPlan.created_at.desc())
    )).scalars().all())


async def get_user_plan(
    db: AsyncSession, *, user_id: str, plan_id: str
) -> WorkoutPlan | None:
    return await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == plan_id,
            WorkoutPlan.user_id == user_id,
        )
    )


async def build_plan_detail(
    db: AsyncSession,
    plan: WorkoutPlan,
    *,
    profile: UserProfile | None = None,
) -> WorkoutPlanDetail:
    exercises = (await db.execute(
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == plan.id)
        .order_by(PlannedExercise.day_of_week, PlannedExercise.order_index)
    )).scalars().all()

    exercise_ids = {item.exercise_id for item in exercises}
    names: dict[str, str] = {}
    if exercise_ids:
        rows = (await db.execute(
            select(Exercise.id, Exercise.name_zh).where(Exercise.id.in_(exercise_ids))
        )).all()
        names = dict(rows)

    result = WorkoutPlanDetail.model_validate(plan)
    result.exercises = [
        PlannedExerciseResponse(
            id=item.id,
            plan_id=item.plan_id,
            exercise_id=item.exercise_id,
            exercise_name=names.get(item.exercise_id),
            day_of_week=item.day_of_week,
            sets=item.sets,
            reps=item.reps,
            rest_seconds=item.rest_seconds,
            recommended_weight_kg=item.recommended_weight_kg,
            order_index=item.order_index,
        )
        for item in exercises
    ]
    from app.services.plan_safety import evaluate_plan_safety

    safety = await evaluate_plan_safety(
        db,
        plan=plan,
        profile=profile,
        planned_exercises=list(exercises),
    )
    result.safety_status = safety.status
    result.safety_reasons = list(safety.reasons)
    from app.config import settings

    result.manual_proposals_enabled = settings.MANUAL_PLAN_PROPOSALS_ENABLED
    return result


async def get_active_user_session(
    db: AsyncSession, *, user_id: str
) -> WorkoutSession | None:
    return await db.scalar(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "in_progress",
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(1)
    )


async def list_user_workout_sessions(
    db: AsyncSession, *, user_id: str, limit: int | None = None
) -> list[WorkoutSession]:
    query = (
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .order_by(WorkoutSession.trained_at.desc(), WorkoutSession.created_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list((await db.execute(query)).scalars().all())


async def get_user_workout_session(
    db: AsyncSession, *, user_id: str, session_id: str
) -> WorkoutSession | None:
    return await db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id,
            WorkoutSession.user_id == user_id,
        )
    )


async def get_completed_exercise_history(
    db: AsyncSession,
    *,
    user_id: str,
    exercise_ids: set[str],
    exclude_session_id: str | None = None,
) -> dict[str, list[SessionExercise]]:
    history_by_exercise: dict[str, list[SessionExercise]] = {
        exercise_id: [] for exercise_id in exercise_ids
    }
    if not exercise_ids:
        return history_by_exercise

    conditions = [
        WorkoutSession.user_id == user_id,
        WorkoutSession.status == "completed",
        SessionExercise.exercise_id.in_(exercise_ids),
    ]
    if exclude_session_id is not None:
        conditions.append(WorkoutSession.id != exclude_session_id)
    rows = (await db.execute(
        select(SessionExercise)
        .join(WorkoutSession, WorkoutSession.id == SessionExercise.session_id)
        .where(*conditions)
        .order_by(
            WorkoutSession.completed_at.desc().nullslast(),
            WorkoutSession.created_at.desc(),
        )
    )).scalars().all()
    for item in rows:
        history_by_exercise[item.exercise_id].append(item)
    return history_by_exercise


async def get_personal_record_baseline(
    db: AsyncSession, *, user_id: str, exercise_id: str
) -> list[dict]:
    history = await get_completed_exercise_history(
        db,
        user_id=user_id,
        exercise_ids={exercise_id},
    )
    return [
        recorded_set
        for item in history.get(exercise_id, [])
        for recorded_set in normalized_sets(item.sets_data)
    ]


async def build_session_detail(
    db: AsyncSession, session: WorkoutSession
) -> WorkoutSessionDetail:
    exercises = (await db.execute(
        select(SessionExercise)
        .where(SessionExercise.session_id == session.id)
        .order_by(SessionExercise.order_index, SessionExercise.id)
    )).scalars().all()

    exercise_ids = {item.exercise_id for item in exercises}
    names: dict[str, str] = {}
    if exercise_ids:
        rows = (await db.execute(
            select(Exercise.id, Exercise.name_zh).where(Exercise.id.in_(exercise_ids))
        )).all()
        names = dict(rows)

    history_by_exercise = await get_completed_exercise_history(
        db,
        user_id=session.user_id,
        exercise_ids=exercise_ids,
        exclude_session_id=session.id,
    )

    plan_name = session.plan_name
    if plan_name is None and session.plan_id:
        plan_name = await db.scalar(
            select(WorkoutPlan.name).where(WorkoutPlan.id == session.plan_id)
        )

    response_exercises = []
    total_sets = 0
    total_reps = 0
    total_volume = 0.0
    for item in exercises:
        sets_data = normalized_sets(item.sets_data)
        history = history_by_exercise.get(item.exercise_id, [])
        previous_sets = normalized_sets(history[0].sets_data) if history else []
        all_sets = [
            history_set
            for history_item in history
            for history_set in normalized_sets(history_item.sets_data)
        ]
        best_weight, best_reps = best_performance([*all_sets, *sets_data])
        sets_count, reps, volume = sets_metrics(sets_data)
        total_sets += sets_count
        total_reps += reps
        total_volume += volume
        response_exercises.append(SessionExerciseResponse(
            id=item.id,
            session_id=item.session_id,
            exercise_id=item.exercise_id,
            exercise_name=names.get(item.exercise_id),
            order_index=item.order_index,
            target_sets=item.target_sets,
            target_reps=item.target_reps,
            target_weight_kg=item.target_weight_kg,
            rest_seconds=item.rest_seconds,
            sets_data=sets_data,
            previous_sets_data=previous_sets,
            personal_best_weight_kg=best_weight,
            personal_best_reps=best_reps,
        ))

    result = WorkoutSessionDetail.model_validate(session)
    result.plan_name = plan_name
    result.exercises = response_exercises
    result.total_sets = total_sets
    result.total_reps = total_reps
    result.total_volume_kg = round(total_volume, 1)
    feedback_data = session.feedback_data if isinstance(session.feedback_data, dict) else {}
    result.feedback = WorkoutFeedback.model_validate(feedback_data) if feedback_data else None
    adjustment_data = session.adjustments_data if isinstance(session.adjustments_data, list) else []
    result.adjustments = [
        WorkoutAdjustmentResponse.model_validate(item)
        for item in adjustment_data
        if isinstance(item, dict)
    ]
    return result


async def get_workout_progress_summary(
    db: AsyncSession,
    *,
    user_id: str,
    weeks: int,
    today: date | None = None,
) -> WorkoutProgressResponse:
    today = today or date.today()
    current_week = today - timedelta(days=today.weekday())
    first_week = current_week - timedelta(weeks=weeks - 1)
    sessions = (await db.execute(
        select(WorkoutSession).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "completed",
            WorkoutSession.trained_at >= first_week,
        )
    )).scalars().all()

    buckets = {
        first_week + timedelta(weeks=index): {
            "sessions": 0,
            "sets": 0,
            "reps": 0,
            "volume": 0.0,
        }
        for index in range(weeks)
    }
    session_weeks: dict[str, date] = {}
    for session in sessions:
        week_start = session.trained_at - timedelta(days=session.trained_at.weekday())
        if week_start in buckets:
            buckets[week_start]["sessions"] += 1
            session_weeks[session.id] = week_start

    if session_weeks:
        exercises = (await db.execute(
            select(SessionExercise).where(SessionExercise.session_id.in_(session_weeks))
        )).scalars().all()
        for exercise in exercises:
            week_start = session_weeks[exercise.session_id]
            sets_count, reps, volume = sets_metrics(exercise.sets_data)
            buckets[week_start]["sets"] += sets_count
            buckets[week_start]["reps"] += reps
            buckets[week_start]["volume"] += volume

    weekly = [
        WeeklyWorkoutProgress(
            week_start=week_start,
            sessions=values["sessions"],
            sets=values["sets"],
            reps=values["reps"],
            volume_kg=round(values["volume"], 1),
        )
        for week_start, values in buckets.items()
    ]
    return WorkoutProgressResponse(
        weeks=weeks,
        total_sessions=sum(item.sessions for item in weekly),
        total_sets=sum(item.sets for item in weekly),
        total_reps=sum(item.reps for item in weekly),
        total_volume_kg=round(sum(item.volume_kg for item in weekly), 1),
        weekly=weekly,
    )
