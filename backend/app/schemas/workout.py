from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, date
from typing import Annotated, Literal


PlanExplanation = Annotated[str, Field(min_length=1, max_length=1000)]


# ── Planned Exercise ──────────────────────────────────────────────────────────

class PlannedExerciseCreate(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(default=3, ge=1, le=8)
    reps: str = Field(default="10", min_length=1, max_length=20)
    rest_seconds: int = Field(default=90, ge=15, le=600)
    order_index: int = Field(default=0, ge=0, le=49)


class PlannedExerciseResponse(BaseModel):
    id: str
    plan_id: str
    exercise_id: str
    exercise_name: str | None = None
    day_of_week: int
    sets: int
    reps: str
    rest_seconds: int
    recommended_weight_kg: float | None = None
    order_index: int
    model_config = {"from_attributes": True}


# ── Workout Plan ──────────────────────────────────────────────────────────────

class WorkoutPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int = Field(default=4, ge=2, le=12)
    days_per_week: int = Field(default=3, ge=1, le=7)
    notes: str | None = Field(default=None, max_length=5000)
    exercises: list[PlannedExerciseCreate] = Field(default_factory=list, max_length=50)


class WorkoutPlanResponse(BaseModel):
    id: str
    user_id: str
    name: str
    goal: str | None
    duration_weeks: int
    days_per_week: int
    is_active: bool
    ai_generated: bool
    notes: str | None
    created_at: datetime
    safety_status: Literal["compatible", "needs_review"] = "compatible"
    safety_reasons: list[str] = Field(default_factory=list)
    manual_proposals_enabled: bool = False
    model_config = {"from_attributes": True}


class WorkoutPlanDetail(WorkoutPlanResponse):
    exercises: list[PlannedExerciseResponse] = Field(default_factory=list)


# ── Personalized Plan Draft ──────────────────────────────────────────────────

class PersonalizedPlanPreviewRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int = Field(default=4, ge=2, le=12)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    session_duration_min: int | None = Field(default=None, ge=20, le=120)


class PersonalizedExerciseOption(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)
    difficulty: str = Field(min_length=1, max_length=20)
    equipment: list[str] = Field(default_factory=list)


class PersonalizedPlanExercise(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(ge=1, le=8)
    reps: str = Field(min_length=1, max_length=20)
    rest_seconds: int = Field(ge=15, le=600)
    order_index: int = Field(ge=0, le=20)


class PersonalizedPlanPreview(BaseModel):
    name: str
    goal: str
    duration_weeks: int
    days_per_week: int
    session_duration_min: int
    rationale: list[PlanExplanation] = Field(default_factory=list)
    safety_notes: list[PlanExplanation] = Field(default_factory=list)
    exercises: list[PersonalizedPlanExercise] = Field(default_factory=list)
    exercise_options: list[PersonalizedExerciseOption] = Field(default_factory=list)
    generation_strategy: str = "profile_rules_v1"


class PersonalizedPlanConfirmRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=50)
    duration_weeks: int = Field(ge=2, le=12)
    days_per_week: int = Field(ge=1, le=7)
    session_duration_min: int = Field(ge=20, le=120)
    rationale: list[PlanExplanation] = Field(default_factory=list, max_length=12)
    safety_notes: list[PlanExplanation] = Field(default_factory=list, max_length=12)
    exercises: list[PersonalizedPlanExercise] = Field(min_length=1, max_length=50)


# ── Workout Session ───────────────────────────────────────────────────────────

class SetData(BaseModel):
    reps: int = Field(ge=1, le=1000)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)


class SessionExerciseCreate(BaseModel):
    exercise_id: str
    sets_data: list[SetData]


class WorkoutSessionCreate(BaseModel):
    trained_at: date
    plan_id: str | None = None
    duration_min: int | None = None
    notes: str | None = None
    exercises: list[SessionExerciseCreate] = Field(default_factory=list)


class WorkoutSessionStart(BaseModel):
    plan_id: str
    day_of_week: int = Field(ge=1, le=7)


class WorkoutSetRecord(BaseModel):
    reps: int = Field(ge=1, le=1000)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)


class WorkoutFeedback(BaseModel):
    difficulty_feedback: Literal["too_easy", "just_right", "too_hard"] | None = None
    perceived_exertion: int | None = Field(default=None, ge=1, le=10)
    energy_level: int | None = Field(default=None, ge=1, le=5)
    pain_level: int = Field(default=0, ge=0, le=10)
    pain_areas: list[str] = Field(default_factory=list, max_length=8)
    feedback_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("pain_areas")
    @classmethod
    def normalize_pain_areas(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 50 for item in normalized):
            raise ValueError("疼痛部位描述过长")
        return normalized

    @model_validator(mode="after")
    def validate_pain_context(self):
        if self.pain_level > 0 and not self.pain_areas:
            raise ValueError("记录疼痛时请选择疼痛部位")
        if self.pain_level == 0 and self.pain_areas:
            raise ValueError("疼痛等级为 0 时不应填写疼痛部位")
        return self


class WorkoutSessionComplete(WorkoutFeedback):
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    notes: str | None = Field(default=None, max_length=2000)


class SessionExerciseResponse(BaseModel):
    id: str
    session_id: str
    exercise_id: str
    exercise_name: str | None = None
    order_index: int
    target_sets: int | None
    target_reps: str | None
    target_weight_kg: float | None = None
    rest_seconds: int | None
    sets_data: list[dict] = Field(default_factory=list)
    previous_sets_data: list[dict] = Field(default_factory=list)
    personal_best_weight_kg: float | None = None
    personal_best_reps: int | None = None
    model_config = {"from_attributes": True}


class WorkoutSessionResponse(BaseModel):
    id: str
    user_id: str
    plan_id: str | None
    day_of_week: int | None
    status: str
    trained_at: date
    duration_min: int | None
    notes: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class WorkoutAdjustmentResponse(BaseModel):
    exercise_id: str
    exercise_name: str
    action: str
    before: dict
    after: dict
    reason: str
    safety_priority: bool = False


class WorkoutSessionDetail(WorkoutSessionResponse):
    plan_name: str | None = None
    total_sets: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0
    exercises: list[SessionExerciseResponse] = Field(default_factory=list)
    feedback: WorkoutFeedback | None = None
    adjustments: list[WorkoutAdjustmentResponse] = Field(default_factory=list)


class WeeklyWorkoutProgress(BaseModel):
    week_start: date
    sessions: int = 0
    sets: int = 0
    reps: int = 0
    volume_kg: float = 0


class WorkoutProgressResponse(BaseModel):
    weeks: int
    total_sessions: int
    total_sets: int
    total_reps: int
    total_volume_kg: float
    weekly: list[WeeklyWorkoutProgress]


# ── AI Generate ───────────────────────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    goal: str | None = None
    duration_weeks: int = 4
