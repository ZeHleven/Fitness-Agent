from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.agent_change_validation import validate_semantic_changes


IntentName = Literal[
    "general_qa",
    "profile_query",
    "health_query",
    "plan_query",
    "next_workout_query",
    "active_workout_query",
    "workout_history_query",
    "workout_progress_query",
    "weight_history_query",
    "nutrition_today_query",
    "nutrition_history_query",
    "food_search_query",
]

IntentDomain = Literal[
    "general",
    "profile",
    "health",
    "workout_plan",
    "workout_session",
    "workout_history",
    "workout_progress",
    "nutrition",
]
RequestKind = Literal[
    "query", "assessment", "generation", "mutation", "proposal_decision"
]
RequestedEffect = Literal["read", "create", "update", "delete", "decide"]
ChangeOperation = Literal["create", "update", "delete"]
RequestedOutput = Literal["answer", "daily_meal_plan"]
EvidenceRequirement = Literal[
    "profile_summary",
    "health_screening",
    "weight_history",
    "active_plan",
    "workout_progress",
    "workout_daily_context",
    "nutrition_today",
    "nutrition_history",
    "nutrition_recent_context",
    "food_catalog",
]


class ChangeRequest(BaseModel):
    """Untrusted semantic change request produced by the understanding layer.

    This is deliberately domain-generic. A separate server-owned capability
    compiler decides whether a request is executable and converts it to a
    narrow Proposal draft.
    """

    model_config = ConfigDict(extra="forbid")

    resource: IntentDomain
    operation: ChangeOperation
    field_path: str | None = Field(default=None, max_length=120)
    target_reference: str | None = Field(default=None, max_length=120)
    value: Any = None
    preserve_unspecified: bool = True


class ResolvedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=120)
    resolved_value: str = Field(min_length=1, max_length=500)
    reference_type: Literal[
        "message",
        "exercise",
        "plan",
        "time_range",
        "metric",
        "other",
    ] = "other"
    source: Literal[
        "current_message",
        "recent_conversation",
        "pending_clarification",
    ] = "recent_conversation"


class IntentResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_intent: IntentName
    intent_domain: IntentDomain | None = None
    request_kind: RequestKind = "query"
    requested_effect: RequestedEffect = "read"
    change_requests: list[ChangeRequest] = Field(default_factory=list, max_length=12)
    evidence_requirements: list[EvidenceRequirement] = Field(
        default_factory=list, max_length=6
    )
    requested_output: RequestedOutput = "answer"
    resolved_query: str = Field(default="", max_length=4000)
    references: list[ResolvedReference] = Field(default_factory=list, max_length=12)
    expanded_intents: list[IntentName] = Field(default_factory=list, max_length=7)
    subtasks: list[str] = Field(default_factory=list, max_length=8)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    clarification_required: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    risk_level: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def fill_compatible_semantics(self) -> "IntentResolution":
        if self.intent_domain is None:
            self.intent_domain = _DOMAIN_BY_INTENT[self.primary_intent]
        return self


class ExplicitPlanAdjustmentCommand(BaseModel):
    """Server-owned typed command for the first explicit write cohort."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["set_duration_weeks"] = "set_duration_weeks"
    expected_duration_weeks: int = Field(ge=2, le=12)
    target_duration_weeks: int = Field(ge=2, le=12)
    preserve_other_fields: Literal[True] = True

    @model_validator(mode="after")
    def validate_effect(self) -> "ExplicitPlanAdjustmentCommand":
        if self.expected_duration_weeks == self.target_duration_weeks:
            raise ValueError("explicit plan adjustment must have an effect")
        return self


@dataclass(frozen=True)
class IntentAttemptTiming:
    attempt: int
    latency_ms: int
    status: Literal["success", "error"]
    error_category: str | None = None


@dataclass(frozen=True)
class IntentResolverOutcome:
    resolution: IntentResolution
    source: Literal["model", "rules"]
    attempt_count: int = 0
    fallback_reason: str | None = None
    error_category: str | None = None
    latency_ms: int = 0
    attempt_timings: tuple[IntentAttemptTiming, ...] = ()


_INTENT_KEYWORDS: tuple[tuple[IntentName, tuple[str, ...]], ...] = (
    ("health_query", ("伤病", "慢性病", "健康筛查", "禁忌", "疼痛", "疼", "痛", "膝盖", "肩膀", "不舒服")),
    ("next_workout_query", ("下一练", "下次练", "下次该练", "下次应该练", "今天练什么", "接下来练什么")),
    (
        "active_workout_query",
        (
            "进行中的训练",
            "正在训练",
            "练到哪",
            "已经记录",
            "当前这次训练",
            "没做完",
            "未完成训练",
            "接着练",
            "继续上次",
        ),
    ),
    ("workout_progress_query", ("训练进度", "训练量", "周进度", "完成了多少", "坚持得怎么样")),
    ("workout_history_query", ("训练历史", "训练记录", "最近练", "以前练", "过去训练")),
    ("weight_history_query", ("体重历史", "体重记录", "体重趋势", "最近体重", "体重变化")),
    ("nutrition_history_query", ("饮食历史", "饮食记录", "最近吃", "过去吃", "营养历史")),
    ("nutrition_today_query", ("今天吃", "今日饮食", "今天的饮食", "今日营养", "今天摄入")),
    ("food_search_query", ("搜索食品", "查找食品", "食品库", "多少热量", "营养成分")),
    ("plan_query", ("训练计划", "当前计划", "计划安排", "一周怎么练", "计划是什么")),
    ("profile_query", ("我的资料", "个人资料", "身高", "体重", "训练目标", "训练偏好", "训练经验")),
)

_ACTIVE_CONTINUATION_MARKERS = (
    "没做完",
    "未完成训练",
    "接着练",
    "继续上次",
)

_NEXT_WORKOUT_MARKERS = (
    "下一练",
    "下次练",
    "下次该练",
    "下次应该练",
)

_PLAN_ADJUSTMENT_MARKERS = (
    "调整",
    "太激进",
    "激进",
    "适合我",
    "合适",
)

_EXPLICIT_PROPOSAL_MARKERS = (
    "待确认提案",
    "调整提案",
    "生成提案",
)

_EXPLICIT_PROPOSAL_SUBTASK = (
    "根据用户明确范围形成待确认的训练计划调整提案"
)

_EXPLICIT_DURATION_CHANGE_PATTERN = re.compile(
    r"(?:训练计划(?:的)?周期|计划周期)"
    r"从(?P<before>\d{1,2})周"
    r"(?P<verb>延长|缩短|调整|修改|改)?"
    r"(?:到|至|为|成)"
    r"(?P<after>\d{1,2})周"
)

_EXPLICIT_PRESERVE_OTHER_FIELDS_PATTERN = re.compile(
    r"(?:其他|其它|其余)内容(?:完全)?(?:保持)?不变"
)

_EXPLICIT_UNSUPPORTED_SCOPE_MARKERS = (
    "组数",
    "次数",
    "重量",
    "休息",
    "替换动作",
    "增加动作",
    "删除动作",
    "频率",
    "训练天数",
)

_RECENT_COMPLETION_MARKERS = (
    "最近四周",
    "最近训练",
    "实际完成",
    "完成情况",
    "训练情况",
)

_PERSONAL_FIT_MARKERS = (
    "太激进",
    "激进",
    "适合我",
    "合适",
)

_HIGH_RISK_KEYWORDS = (
    "胸痛",
    "胸口痛",
    "呼吸困难",
    "喘不上气",
    "晕厥",
    "昏厥",
    "失去意识",
    "剧烈疼痛",
    "严重急性疼痛",
)

_UNSUPPORTED_WRITE_KEYWORDS = (
    "替我完成训练",
    "帮我完成训练",
    "完成训练并保存",
    "保存这次训练",
    "替我记录",
    "帮我记录",
    "修改训练计划",
    "修改我的计划",
    "替我开始训练",
    "帮我开始训练",
)

_EXPLICIT_PLAN_KEYWORDS = (
    "当前计划",
    "训练计划",
    "计划安排",
    "我的计划",
)

_AFFIRMATIVE_FOLLOWUPS = frozenset({
    "需要",
    "要",
    "好",
    "好的",
    "可以",
    "行",
    "是",
    "是的",
    "对",
    "对的",
    "麻烦了",
    "帮我查",
    "查一下",
    "继续",
})

_ASSISTANT_OFFER_MARKERS = (
    "需要我",
    "要我",
    "是否需要",
    "要不要",
    "可以帮你",
    "帮你查",
    "我来查",
)

_CLARIFICATION_CANCEL_MESSAGES = frozenset({
    "不用",
    "不用了",
    "算了",
    "取消",
    "先不查了",
})

_CLARIFICATION_NON_ANSWERS = frozenset({
    "不知道",
    "不确定",
    "没想好",
    "随便",
    "都可以",
    "你看着办",
})

_TIME_VALUE_MARKERS = (
    "今天",
    "昨天",
    "本周",
    "上周",
    "这周",
    "最近",
    "过去",
    "天",
    "周",
    "月",
    "年",
    "次",
)

_METRIC_VALUE_MARKERS = (
    "重量",
    "次数",
    "组数",
    "容量",
    "训练量",
    "频率",
    "趋势",
    "完成度",
)

_CANONICAL_QUERY_BY_INTENT: dict[IntentName, str] = {
    "general_qa": "回答当前健身问题",
    "profile_query": "查询我的个人训练资料",
    "health_query": "查询我的健康筛查与训练安全信息",
    "plan_query": "查询我的当前训练计划",
    "next_workout_query": "查询我下一练应该做什么",
    "active_workout_query": "查询我正在进行的训练",
    "workout_history_query": "查询我的训练历史",
    "workout_progress_query": "查询我的训练进度",
    "weight_history_query": "查询我的体重历史",
    "nutrition_today_query": "查询我今天的饮食与营养汇总",
    "nutrition_history_query": "查询我的饮食历史",
    "food_search_query": "搜索食品库",
}

_DOMAIN_BY_INTENT: dict[IntentName, IntentDomain] = {
    "general_qa": "general",
    "profile_query": "profile",
    "health_query": "health",
    "plan_query": "workout_plan",
    "next_workout_query": "workout_session",
    "active_workout_query": "workout_session",
    "workout_history_query": "workout_history",
    "workout_progress_query": "workout_progress",
    "weight_history_query": "profile",
    "nutrition_today_query": "nutrition",
    "nutrition_history_query": "nutrition",
    "food_search_query": "nutrition",
}

_INTENT_BY_DOMAIN: dict[IntentDomain, IntentName] = {
    "general": "general_qa",
    "profile": "profile_query",
    "health": "health_query",
    "workout_plan": "plan_query",
    "workout_session": "active_workout_query",
    "workout_history": "workout_history_query",
    "workout_progress": "workout_progress_query",
    "nutrition": "nutrition_today_query",
}

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}

_MUTATION_VERB_PATTERN = re.compile(
    r"(?:调整|修改|更新|改成|改为|改到|设成|设为|设置|增加|新增|"
    r"新建|添加|写入|录入|减少|降低|调低|调高|降一点|缩短|延长|"
    r"删除|移除|替换|创建|保存|记录|开始|完成)"
)
_NEGATED_MUTATION_PATTERN = re.compile(
    r"(?:不要|别|先别|无需|不用|不必|暂不|别再).{0,12}?"
    r"(?:调整|修改|更新|改成|改为|设置|增加|新增|新建|添加|写入|"
    r"录入|减少|降低|删除|移除|替换|创建|保存|记录|开始|完成)"
)
_CREATE_VERB_PATTERN = re.compile(r"(?:新增|新建|添加|创建|写入|录入)")
_DELETE_VERB_PATTERN = re.compile(r"(?:删除|移除)")
_HOW_TO_PREFIX_PATTERN = re.compile(r"^(?:怎样|怎么|如何|应该怎样|应该怎么)")
_CONFIRM_DECISION_PATTERN = re.compile(
    r"(?<!待)(?:确认|同意|接受|应用|执行|提交)"
)
_REJECT_DECISION_PATTERN = re.compile(r"(?:拒绝|不同意|取消|放弃)")
_PROPOSAL_REFERENCE_PATTERN = re.compile(
    r"(?:提案|方案|调整|改动|变更|刚才|上一个|这个)"
)
_PLAN_DOMAIN_PATTERN = re.compile(
    r"(?:训练计划|当前计划|我的计划|计划周期|训练频率|训练天数|"
    r"每周.{0,6}(?:天|次)|\d{1,2}组|组数|每组|次数|休息|重量|公斤|kg)"
)
_PROFILE_DOMAIN_PATTERN = re.compile(
    r"(?:个人资料|我的资料|身高|体重|训练目标|训练偏好|训练经验)"
)
_NUTRITION_DOMAIN_PATTERN = re.compile(
    r"(?:饮食|营养|餐食|三餐|食谱|早餐|午餐|晚餐|热量|蛋白质|怎么吃|配餐)"
)
_SESSION_DOMAIN_PATTERN = re.compile(
    r"(?:这次训练|当前训练|进行中的训练|训练组|开始训练|完成训练|保存训练|记录训练)"
)
_HISTORY_DOMAIN_PATTERN = re.compile(r"(?:训练历史|训练记录|过去训练|最近练过)")
_PROGRESS_DOMAIN_PATTERN = re.compile(r"(?:训练进度|周进度|完成率|训练量|容量趋势)")
_ASSESSMENT_PATTERN = re.compile(
    r"(?:评估|分析|是否适合|合不合适|太激进|完成情况|执行情况)"
)
_GENERATION_PATTERN = re.compile(
    r"(?:制定|安排|推荐|设计|规划|搭配|配|做一份|给一份).{0,24}?"
    r"(?:饮食|三餐|早餐|午餐|晚餐|餐食|食谱|怎么吃)|"
    r"(?:饮食|三餐|餐食|食谱).{0,24}?(?:制定|安排|推荐|设计|规划|搭配)"
)
_DAILY_MEAL_SCOPE_PATTERN = re.compile(
    r"(?:今天|今日|全天|一天|三餐|饮食方案|一份.{0,8}(?:饮食|食谱)|"
    r"(?:增肌|减脂|减重|力量).{0,8}(?:饮食|食谱)|"
    r"早餐.{0,16}午餐.{0,16}晚餐)"
)
_ARTIFACT_SAVE_PATTERN = re.compile(
    r"(?:保存|记录|提交).{0,16}?(?:这份|这个|刚才|上面|上述|当前)?"
    r".{0,8}?(?:饮食)?方案|"
    r"(?:这份|这个|刚才|上面|上述|当前).{0,8}?(?:饮食)?方案"
    r".{0,16}?(?:保存|记录|提交)"
)
_ARTIFACT_REVISION_PATTERN = re.compile(
    r"(?:调整|修改|改一下|换掉|替换).{0,16}?(?:这份|这个|刚才|上面|上述)"
    r".{0,8}?(?:饮食)?方案|"
    r"(?:这份|这个|刚才|上面|上述).{0,8}?(?:饮食)?方案"
    r".{0,16}?(?:调整|修改|改一下|换掉|替换)"
)

_DAILY_MEAL_EVIDENCE: list[EvidenceRequirement] = [
    "profile_summary",
    "health_screening",
    "weight_history",
    "workout_daily_context",
    "nutrition_recent_context",
    "food_catalog",
]

_EVIDENCE_BY_INTENT: dict[IntentName, tuple[EvidenceRequirement, ...]] = {
    "general_qa": (),
    "profile_query": ("profile_summary",),
    "health_query": ("health_screening",),
    "plan_query": ("active_plan",),
    "next_workout_query": ("workout_daily_context",),
    "active_workout_query": (),
    "workout_history_query": (),
    "workout_progress_query": ("workout_progress",),
    "weight_history_query": ("weight_history",),
    "nutrition_today_query": ("nutrition_today",),
    "nutrition_history_query": ("nutrition_history",),
    "food_search_query": ("food_catalog",),
}


def _is_daily_meal_generation(message: str) -> bool:
    normalized = message.strip().lower()
    return bool(
        _NUTRITION_DOMAIN_PATTERN.search(normalized)
        and _GENERATION_PATTERN.search(normalized)
        and _DAILY_MEAL_SCOPE_PATTERN.search(normalized)
    )


def _is_artifact_save_request(message: str) -> bool:
    return bool(_ARTIFACT_SAVE_PATTERN.search(message.strip().lower()))


def _is_artifact_revision_request(message: str) -> bool:
    return bool(_ARTIFACT_REVISION_PATTERN.search(message.strip().lower()))

_FREQUENCY_TARGET_PATTERNS = (
    re.compile(
        r"(?:每周|一周)\s*(?:训练|练)?\s*"
        r"(?P<value>[1-7一二三四五六七])\s*(?:天|次)"
    ),
    re.compile(
        r"(?:训练频率|训练天数|每周天数).{0,12}?"
        r"(?P<value>[1-7一二三四五六七])\s*(?:天|次)"
    ),
)
_DURATION_TARGET_PATTERN = re.compile(
    r"(?P<value>\d{1,2}|十一|十二|[一二三四五六七八九十])\s*周"
)
_EXERCISE_REFERENCE_PATTERNS = (
    re.compile(
        r"(?:把|将)\s*(?P<name>[\u4e00-\u9fffA-Za-z0-9·_-]{1,30}?)"
        r"(?:的)?(?:组数|次数|每组|休息(?:时间)?|重量|调整|修改|改|设)"
    ),
    re.compile(
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·_-]{1,30}?)(?:的)?"
        r"(?:组数|每组次数|休息时间|训练重量)"
    ),
)


def _semantic_number(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return _CHINESE_DIGITS.get(raw)


def _infer_domain(message: str, fallback_intent: IntentName) -> IntentDomain:
    normalized = message.strip().lower()
    if contains_health_red_flag(message) or any(
        keyword in normalized for keyword in _INTENT_KEYWORDS[0][1]
    ):
        return "health"
    if "体重" in normalized:
        return "profile"
    if _PLAN_DOMAIN_PATTERN.search(normalized):
        return "workout_plan"
    if _PROFILE_DOMAIN_PATTERN.search(normalized):
        return "profile"
    if _NUTRITION_DOMAIN_PATTERN.search(normalized):
        return "nutrition"
    if _SESSION_DOMAIN_PATTERN.search(normalized):
        return "workout_session"
    if _HISTORY_DOMAIN_PATTERN.search(normalized):
        return "workout_history"
    if _PROGRESS_DOMAIN_PATTERN.search(normalized):
        return "workout_progress"
    return _DOMAIN_BY_INTENT[fallback_intent]


def _proposal_decision_action(message: str) -> Literal["confirm", "reject"] | None:
    normalized = message.strip().lower()
    if not _PROPOSAL_REFERENCE_PATTERN.search(normalized):
        return None
    confirms = bool(_CONFIRM_DECISION_PATTERN.search(normalized))
    rejects = bool(_REJECT_DECISION_PATTERN.search(normalized))
    if confirms == rejects:
        return None
    return "confirm" if confirms else "reject"


def _exercise_reference(message: str) -> str | None:
    for pattern in _EXERCISE_REFERENCE_PATTERNS:
        match = pattern.search(message)
        if match is not None:
            value = match.group("name").strip(" 的")
            if value:
                return value[:120]
    return None


def _extract_plan_change_requests(message: str) -> list[ChangeRequest]:
    normalized = re.sub(r"\s+", "", message.strip().lower())
    operation: ChangeOperation = (
        "create"
        if _CREATE_VERB_PATTERN.search(normalized)
        else "delete"
        if _DELETE_VERB_PATTERN.search(normalized)
        else "update"
    )
    changes: list[ChangeRequest] = []

    frequency_value: int | None = None
    for pattern in _FREQUENCY_TARGET_PATTERNS:
        match = pattern.search(normalized)
        if match is not None:
            frequency_value = _semantic_number(match.group("value"))
            break
    if frequency_value is not None or any(
        marker in normalized
        for marker in ("训练频率", "训练天数", "每周练", "每周训练")
    ):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.days_per_week",
            value=frequency_value,
        ))

    if any(marker in normalized for marker in ("周期", "延长", "缩短")):
        duration_values = [
            _semantic_number(match.group("value"))
            for match in _DURATION_TARGET_PATTERN.finditer(normalized)
        ]
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.duration_weeks",
            value=duration_values[-1] if duration_values else None,
        ))

    exercise_reference = _exercise_reference(message)
    exercise_fields: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("exercise.sets", re.compile(r"(?P<value>\d{1,2})组")),
        (
            "exercise.reps",
            re.compile(r"(?:每组)?(?P<value>\d{1,3}(?:[-~～至到]\d{1,3})?)次"),
        ),
        (
            "exercise.rest_seconds",
            re.compile(r"(?:休息(?:时间)?(?:改|调|设)?(?:成|为|到)?)?(?P<value>\d{1,3})秒"),
        ),
        (
            "exercise.recommended_weight_kg",
            re.compile(r"(?P<value>\d{1,3}(?:\.\d+)?)\s*(?:kg|公斤|千克)"),
        ),
    )
    for field_path, pattern in exercise_fields:
        field_marker = field_path.rsplit(".", 1)[-1]
        marker_present = {
            "sets": (
                "组数" in normalized
                or re.search(r"\d{1,2}组(?!次)", normalized) is not None
            ),
            "reps": "次数" in normalized or "每组" in normalized,
            "rest_seconds": "休息" in normalized,
            "recommended_weight_kg": (
                "重量" in normalized or "kg" in normalized or "公斤" in normalized
            ),
        }[field_marker]
        if not marker_present or frequency_value is not None and field_path == "exercise.reps":
            continue
        match = pattern.search(normalized)
        value: Any = match.group("value") if match is not None else None
        if value is not None:
            if field_path in {"exercise.sets", "exercise.rest_seconds"}:
                value = int(value)
            elif field_path == "exercise.recommended_weight_kg":
                value = float(value)
            elif field_path == "exercise.reps":
                value = re.sub(r"[~～至到]", "-", value)
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path=field_path,
            target_reference=exercise_reference,
            value=value,
        ))

    if any(marker in normalized for marker in ("替换动作", "更换动作")):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="exercises.replace",
            target_reference=exercise_reference,
        ))
    elif any(marker in normalized for marker in ("新增动作", "添加动作")):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="create",
            field_path="exercises",
        ))
    elif any(marker in normalized for marker in ("删除动作", "移除动作")):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="delete",
            field_path="exercises",
            target_reference=exercise_reference,
        ))
    elif any(marker in normalized for marker in ("新增训练日", "添加训练日")):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="create",
            field_path="schedule.training_days",
        ))
    elif any(marker in normalized for marker in ("删除训练日", "移除训练日")):
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation="delete",
            field_path="schedule.training_days",
        ))

    if not changes:
        changes.append(ChangeRequest(
            resource="workout_plan",
            operation=operation,
            field_path=None,
        ))
    return changes[:12]


def _infer_request_semantics(
    message: str,
    *,
    fallback_intent: IntentName,
) -> tuple[
    IntentDomain,
    RequestKind,
    RequestedEffect,
    list[ChangeRequest],
    list[str],
    str | None,
]:
    normalized = message.strip().lower()
    domain = _infer_domain(message, fallback_intent)
    decision_action = _proposal_decision_action(message)
    if decision_action is not None and not contains_health_red_flag(message):
        return (
            domain,
            "proposal_decision",
            "decide",
            [ChangeRequest(
                resource=domain,
                operation="update",
                field_path="proposal.status",
                target_reference="latest_pending_in_conversation",
                value=decision_action,
            )],
            [],
            None,
        )

    # A compound first request such as “制定并保存” must still stop at an
    # inspectable Artifact.  Saving is only meaningful when it refers to an
    # already displayed plan and does not also ask to generate one.
    if _is_daily_meal_generation(message) or _is_artifact_revision_request(message):
        return "nutrition", "generation", "read", [], [], None

    if _is_artifact_save_request(message):
        return (
            "nutrition",
            "mutation",
            "create",
            [ChangeRequest(
                resource="nutrition",
                operation="create",
                field_path="daily_meal_plan.save",
                target_reference="latest_active_artifact_in_conversation",
                value=None,
            )],
            [],
            None,
        )

    if _is_active_continuation_comparison(message):
        return domain, "query", "read", [], [], None
    if (
        _ASSESSMENT_PATTERN.search(normalized)
        and re.search(r"(?:是否|判断|看看|建议|评估|分析)", normalized)
        and not re.search(
            r"(?:改成|改为|改到|设成|设为|设置|增加|减少|降低|调低|"
            r"调高|缩短|延长|删除|移除|替换)",
            normalized,
        )
    ):
        return domain, "assessment", "read", [], [], None

    mutation_requested = bool(_MUTATION_VERB_PATTERN.search(normalized))
    if _NEGATED_MUTATION_PATTERN.search(normalized):
        mutation_requested = False
    if (
        "记录" in normalized
        and re.search(r"(?:查看|查询|看看|历史|最近|过去|有哪些|是什么)", normalized)
        and not re.search(
            r"(?:调整|修改|更新|改成|改为|改到|设成|设为|设置|增加|新增|"
            r"新建|添加|写入|录入|减少|降低|调低|调高|缩短|延长|"
            r"删除|移除|替换|制定|创建|保存)",
            normalized,
        )
    ):
        mutation_requested = False
    advice_or_question = bool(
        _HOW_TO_PREFIX_PATTERN.search(normalized)
        or re.search(
            r"(?:怎样|怎么|如何|是否|能否|还能|应该|什么|多少|第几|"
            r"哪里|哪一|吗[？?。]?$)",
            normalized,
        )
    )
    direct_polite_mutation = bool(re.search(
        r"(?:把|将).{0,80}?"
        r"(?:改成|改为|改到|设成|设为|设置为|调整为|调整到).{0,30}?"
        r"(?:\d|[一二三四五六七八九十])",
        normalized,
    ))
    if advice_or_question and not direct_polite_mutation and not re.search(
        r"(?:请|帮我|替我|给我|直接帮我)", normalized
    ):
        mutation_requested = False
    if mutation_requested and not contains_health_red_flag(message):
        effect: RequestedEffect = (
            "create"
            if _CREATE_VERB_PATTERN.search(normalized)
            or ("记录" in normalized and domain in {"profile", "nutrition"})
            else "delete"
            if _DELETE_VERB_PATTERN.search(normalized)
            else "update"
        )
        if domain == "workout_plan":
            changes = _extract_plan_change_requests(message)
        elif domain == "profile":
            weight_match = re.search(
                r"体重.{0,10}?(?P<value>\d{2,3}(?:\.\d+)?)\s*(?:公斤|kg|千克)?",
                normalized,
            )
            changes = [ChangeRequest(
                resource="profile",
                operation="create" if "记录" in normalized else "update",
                field_path=(
                    "weight_log.weight_kg" if "记录" in normalized
                    else "profile.weight_kg"
                ) if weight_match else None,
                value=float(weight_match.group("value")) if weight_match else None,
            )]
        else:
            changes = [ChangeRequest(
                resource=domain,
                operation=effect,
                field_path=None,
            )]
        missing_slots: list[str] = []
        for change in changes:
            if (
                change.field_path is None
                and change.value is None
                and (
                    domain in {"profile", "health", "nutrition"}
                    or (domain == "workout_plan" and change.operation == "update")
                )
            ):
                if domain == "nutrition" and change.operation == "create":
                    missing_slots.append("餐次、食品和克数")
                elif domain == "nutrition" and change.operation == "delete":
                    missing_slots.append("要删除的具体餐次记录")
                else:
                    missing_slots.append("写入对象和具体目标值")
            if (
                domain == "workout_plan"
                and change.operation == "update"
                and change.value is None
            ):
                missing_slots.append("计划调整的具体目标值")
            if (
                change.field_path is not None
                and change.field_path.startswith("exercise.")
                and change.target_reference is None
            ):
                missing_slots.append("要调整的动作名称")
        missing_slots = list(dict.fromkeys(missing_slots))[:8]
        question = None
        if missing_slots:
            question = f"请补充{'、'.join(missing_slots)}，我再为你生成待确认的调整提案。"
        return domain, "mutation", effect, changes, missing_slots, question

    if _ASSESSMENT_PATTERN.search(normalized):
        return domain, "assessment", "read", [], [], None
    return domain, "query", "read", [], [], None

_CONTEXT_REFERENCE_MARKERS = (
    "那个",
    "这个",
    "这项",
    "刚才",
    "上一个",
    "前面",
    "它",
    "那",
)


def contains_health_red_flag(message: str) -> bool:
    normalized = message.strip().lower()
    return any(keyword in normalized for keyword in _HIGH_RISK_KEYWORDS)


def contains_unsupported_write_request(message: str) -> bool:
    normalized = message.strip().lower()
    if any(keyword in normalized for keyword in _UNSUPPORTED_WRITE_KEYWORDS):
        return True
    fallback_intent: IntentName = "general_qa"
    domain, request_kind, effect, changes, _, _ = _infer_request_semantics(
        message,
        fallback_intent=fallback_intent,
    )
    if request_kind != "mutation":
        return False
    if domain in {"profile", "health", "nutrition"}:
        return False
    if domain != "workout_plan" or effect != "update":
        return True
    supported = {
        "schedule.duration_weeks",
        "schedule.days_per_week",
        "exercise.sets",
        "exercise.reps",
        "exercise.rest_seconds",
        "exercise.recommended_weight_kg",
    }
    return any(change.field_path not in supported for change in changes)


def _is_active_continuation_comparison(message: str) -> bool:
    normalized = message.strip().lower()
    return (
        any(marker in normalized for marker in _ACTIVE_CONTINUATION_MARKERS)
        and any(marker in normalized for marker in _NEXT_WORKOUT_MARKERS)
    )


def _is_plan_adjustment_assessment(message: str) -> bool:
    normalized = message.strip().lower()
    return (
        any(marker in normalized for marker in _EXPLICIT_PLAN_KEYWORDS)
        and any(marker in normalized for marker in _PLAN_ADJUSTMENT_MARKERS)
        and any(marker in normalized for marker in _RECENT_COMPLETION_MARKERS)
    )


def parse_explicit_plan_adjustment_command(
    message: str,
) -> ExplicitPlanAdjustmentCommand | None:
    """Parse only an unambiguous, one-field duration Proposal request.

    This parser is intentionally narrower than natural-language intent
    classification. A request reaches the plan-only mutation path only when
    the expected baseline, target value, unchanged remainder, and Proposal
    intent are all explicit.
    """

    normalized = re.sub(r"\s+", "", message.strip().lower())
    if not (
        any(marker in normalized for marker in _EXPLICIT_PLAN_KEYWORDS)
        and any(marker in normalized for marker in _EXPLICIT_PROPOSAL_MARKERS)
        and _EXPLICIT_PRESERVE_OTHER_FIELDS_PATTERN.search(normalized)
        and not any(
            marker in normalized
            for marker in _EXPLICIT_UNSUPPORTED_SCOPE_MARKERS
        )
    ):
        return None

    matches = list(_EXPLICIT_DURATION_CHANGE_PATTERN.finditer(normalized))
    if len(matches) != 1:
        return None
    match = matches[0]
    before = int(match.group("before"))
    after = int(match.group("after"))
    verb = match.group("verb")
    if verb == "延长" and after <= before:
        return None
    if verb == "缩短" and after >= before:
        return None
    try:
        return ExplicitPlanAdjustmentCommand(
            expected_duration_weeks=before,
            target_duration_weeks=after,
        )
    except ValueError:
        return None


def is_explicit_plan_adjustment_request(message: str) -> bool:
    """Recognize a narrow, user-authored request for a confirmable proposal."""
    return parse_explicit_plan_adjustment_command(message) is not None


def is_explicit_plan_adjustment_resolution(
    resolution: IntentResolution,
) -> bool:
    """Backward-compatible name for an explicit workout-plan mutation."""
    return (
        resolution.primary_intent == "plan_query"
        and resolution.intent_domain == "workout_plan"
        and (
            (
                resolution.request_kind == "mutation"
                and resolution.requested_effect == "update"
            )
            or _EXPLICIT_PROPOSAL_SUBTASK in resolution.subtasks
        )
    )


def is_plan_mutation_resolution(resolution: IntentResolution) -> bool:
    return is_explicit_plan_adjustment_resolution(resolution)


def _asks_personal_plan_fit(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in _PERSONAL_FIT_MARKERS)


def resolve_intent(message: str) -> IntentResolution:
    """Deterministic resolver used as a safe fallback for the model classifier."""
    normalized = message.strip().lower()
    matched: list[IntentName] = []
    for intent, keywords in _INTENT_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            matched.append(intent)

    if "weight_history_query" in matched:
        matched = [intent for intent in matched if intent != "profile_query"]

    if contains_health_red_flag(message) and "health_query" not in matched:
        matched.insert(0, "health_query")

    fallback_intent = matched[0] if matched else "general_qa"
    (
        intent_domain,
        request_kind,
        requested_effect,
        change_requests,
        semantic_missing_slots,
        semantic_question,
    ) = _infer_request_semantics(message, fallback_intent=fallback_intent)
    requested_output: RequestedOutput = (
        "daily_meal_plan" if request_kind == "generation" else "answer"
    )
    evidence_requirements: list[EvidenceRequirement] = (
        list(_DAILY_MEAL_EVIDENCE)
        if requested_output == "daily_meal_plan"
        else []
    )

    if request_kind == "generation":
        matched = ["nutrition_today_query"]
    elif request_kind in {"mutation", "proposal_decision"}:
        matched = [
            "general_qa"
            if request_kind == "mutation"
            and intent_domain != "workout_plan"
            and any(
                keyword in normalized for keyword in _UNSUPPORTED_WRITE_KEYWORDS
            )
            else _INTENT_BY_DOMAIN[intent_domain]
        ]
    elif not matched and intent_domain not in {"general", "nutrition"}:
        matched = [_INTENT_BY_DOMAIN[intent_domain]]

    if request_kind == "query" and (
        is_explicit_plan_adjustment_request(message)
        and "health_query" not in matched
    ):
        matched = ["plan_query"]
    elif (
        _is_active_continuation_comparison(message)
        and "health_query" not in matched
    ):
        matched = ["active_workout_query", "next_workout_query"]
    elif (
        _is_plan_adjustment_assessment(message)
        and "health_query" not in matched
    ):
        matched = [
            "plan_query",
            "workout_progress_query",
            "workout_history_query",
        ]
        if _asks_personal_plan_fit(message):
            matched.append("profile_query")

    if not matched:
        return IntentResolution(
            primary_intent="general_qa",
            intent_domain=intent_domain,
            request_kind=request_kind,
            requested_effect=requested_effect,
            change_requests=change_requests,
            evidence_requirements=evidence_requirements,
            requested_output=requested_output,
            resolved_query=message.strip(),
            expanded_intents=[],
            subtasks=(
                ["识别写入请求并进行能力校验"]
                if request_kind == "mutation"
                else ["回答一般健身问题"]
            ),
            missing_slots=semantic_missing_slots,
            clarification_required=bool(semantic_missing_slots),
            clarification_question=semantic_question,
            confidence=0.65,
        )

    primary = matched[0]
    risk_level: Literal["low", "medium", "high"] = (
        "high"
        if contains_health_red_flag(message)
        else "medium"
        if "health_query" in matched
        else "low"
    )
    resolved_query = message.strip()
    subtasks = [f"查询并回答：{intent}" for intent in matched]
    if request_kind == "mutation":
        subtasks = (
            ["读取当前训练计划", _EXPLICIT_PROPOSAL_SUBTASK]
            if is_explicit_plan_adjustment_request(message)
            else ["读取当前训练计划", "校验变更并形成待确认提案"]
            if intent_domain == "workout_plan" and requested_effect == "update"
            else ["识别写入请求并进行能力校验"]
        )
    elif request_kind == "proposal_decision":
        subtasks = ["定位当前会话唯一待确认提案", "安全执行提案决策"]
    elif request_kind == "generation":
        subtasks = ["按需读取个性化证据", "生成并校验全天饮食方案"]
    if (
        _is_active_continuation_comparison(message)
        and primary != "health_query"
    ):
        resolved_query = (
            "检查是否存在未完成的活动训练；若不存在，再查询下一练，"
            "判断现在应该继续上次训练还是开始下一练"
        )
        subtasks = ["检查活动训练", "必要时查询下一练"]
    elif (
        is_explicit_plan_adjustment_request(message)
        and primary != "health_query"
    ):
        subtasks = [
            "读取当前训练计划",
            _EXPLICIT_PROPOSAL_SUBTASK,
        ]
    elif (
        _is_plan_adjustment_assessment(message)
        and primary != "health_query"
    ):
        subtasks = []
        if _asks_personal_plan_fit(message):
            subtasks.append("读取用户训练偏好")
        subtasks.extend([
            "读取当前训练计划",
            "读取近期训练进度，失败时改查历史记录",
            "评估并形成待确认的调整建议",
        ])
    if request_kind == "generation":
        evidence_requirements = list(_DAILY_MEAL_EVIDENCE)
    elif request_kind == "assessment" and intent_domain == "workout_plan":
        evidence_requirements = [
            "active_plan",
            "profile_summary",
            "health_screening",
            "workout_progress",
        ]
    elif request_kind == "mutation" and intent_domain == "workout_plan":
        evidence_requirements = ["active_plan"]
    elif request_kind in {"query", "assessment"}:
        evidence_requirements = list(dict.fromkeys(
            evidence
            for intent in matched
            for evidence in _EVIDENCE_BY_INTENT[intent]
        ))[:6]
    return IntentResolution(
        primary_intent=primary,
        intent_domain=intent_domain,
        request_kind=request_kind,
        requested_effect=requested_effect,
        change_requests=change_requests,
        evidence_requirements=evidence_requirements,
        requested_output=requested_output,
        resolved_query=resolved_query,
        expanded_intents=matched[1:],
        subtasks=subtasks,
        missing_slots=semantic_missing_slots,
        clarification_required=bool(semantic_missing_slots),
        clarification_question=semantic_question,
        risk_level=risk_level,
        confidence=0.9 if len(matched) == 1 else 0.82,
    )


def resolve_contextual_followup(
    message: str,
    context_messages: list[dict[str, str]] | None,
) -> IntentResolution | None:
    """Resolve a small whitelist of explicit follow-ups from the last offer.

    This intentionally does not infer from arbitrary history. Only a short,
    affirmative message immediately following an assistant offer may inherit
    that offer's intent, keeping tool routing narrow and predictable.
    """
    normalized = message.strip().lower().strip("。！？!?，, ")
    if normalized not in _AFFIRMATIVE_FOLLOWUPS or not context_messages:
        return None

    last_message = context_messages[-1]
    if last_message.get("role") != "assistant":
        return None
    assistant_content = str(last_message.get("content") or "").strip()
    marker_positions = [
        assistant_content.find(marker)
        for marker in _ASSISTANT_OFFER_MARKERS
        if marker in assistant_content
    ]
    if not marker_positions:
        return None
    offer_content = assistant_content[min(marker_positions):]

    inherited = resolve_intent(offer_content)
    if inherited.primary_intent == "general_qa":
        return IntentResolution(
            primary_intent="general_qa",
            resolved_query=message.strip(),
            subtasks=["澄清要继续查询的内容"],
            missing_slots=["要继续查询的具体内容"],
            clarification_required=True,
            clarification_question="你希望我继续查询哪一项内容？",
            confidence=0.7,
        )
    return inherited.model_copy(update={
        "resolved_query": "；".join(
            _CANONICAL_QUERY_BY_INTENT[intent]
            for intent in [
                inherited.primary_intent,
                *inherited.expanded_intents,
            ]
        ),
        "references": [ResolvedReference(
            expression=message.strip(),
            resolved_value=assistant_content[:500],
            reference_type="message",
            source="recent_conversation",
        )],
        "subtasks": [f"承接上一轮：{item}" for item in inherited.subtasks],
        "confidence": min(inherited.confidence, 0.88),
    })


def resolve_pending_clarification(
    message: str,
    pending_clarification: dict | None,
) -> tuple[IntentResolution, str] | None:
    """Fill a single pending slot, cancel it, or defer complex cases to model."""
    if not pending_clarification:
        return None

    normalized = message.strip().lower().strip("。！？!?，, ")
    if normalized in _CLARIFICATION_CANCEL_MESSAGES:
        return (
            IntentResolution(
                primary_intent="general_qa",
                resolved_query=message.strip(),
                subtasks=["确认取消上一轮查询"],
                confidence=0.98,
            ),
            "clarification_cancelled",
        )
    if normalized in _CLARIFICATION_NON_ANSWERS:
        return None

    direct_resolution = resolve_intent(message)
    if (
        direct_resolution.primary_intent != "general_qa"
        or direct_resolution.risk_level != "low"
        or contains_unsupported_write_request(message)
    ):
        return None

    missing_slots = pending_clarification.get("missing_slots")
    if not isinstance(missing_slots, list) or len(missing_slots) != 1:
        return None
    slot = str(missing_slots[0])[:120]
    if not message.strip() or len(message.strip()) > 500:
        return None
    if (
        ("时间" in slot or "范围" in slot)
        and not any(marker in message for marker in _TIME_VALUE_MARKERS)
        and not any(character.isdigit() for character in message)
    ):
        return None
    if (
        "指标" in slot
        and not any(marker in message for marker in _METRIC_VALUE_MARKERS)
    ):
        return None

    inherited = pending_clarification_to_resolution(pending_clarification)
    if inherited is None:
        return None

    if inherited.request_kind == "mutation":
        # Mutation slot filling must go back through the structured model with
        # the persisted partial change.  Re-parsing a concatenated Chinese
        # sentence with deterministic rules loses nested meal/plan fields and
        # turns rule extraction limits into false user omissions.
        return None

    references = [*inherited.references, ResolvedReference(
        expression=message.strip()[:120],
        resolved_value=f"{slot}：{message.strip()}"[:500],
        reference_type=(
            "time_range" if "时间" in slot or "范围" in slot else "other"
        ),
        source="pending_clarification",
    )]
    return (
        inherited.model_copy(update={
            "resolved_query": (
                f"{inherited.resolved_query}；{slot}：{message.strip()}"
            )[:4000],
            "references": references[:12],
            "missing_slots": [],
            "clarification_required": False,
            "clarification_question": None,
            "confidence": min(max(inherited.confidence, 0.72), 0.9),
        }),
        "clarification_filled",
    )


def pending_clarification_to_resolution(
    pending_clarification: dict | None,
) -> IntentResolution | None:
    if not pending_clarification:
        return None
    try:
        primary_intent: IntentName = pending_clarification["primary_intent"]
        missing_slots = pending_clarification.get("missing_slots") or []
        return IntentResolution(
            primary_intent=primary_intent,
            intent_domain=pending_clarification.get("intent_domain"),
            request_kind=pending_clarification.get("request_kind") or "query",
            requested_effect=(
                pending_clarification.get("requested_effect") or "read"
            ),
            change_requests=pending_clarification.get("change_requests") or [],
            evidence_requirements=(
                pending_clarification.get("evidence_requirements") or []
            ),
            requested_output=(
                pending_clarification.get("requested_output") or "answer"
            ),
            resolved_query=str(
                pending_clarification.get("resolved_query")
                or _CANONICAL_QUERY_BY_INTENT[primary_intent]
            )[:4000],
            references=pending_clarification.get("references") or [],
            expanded_intents=pending_clarification.get("expanded_intents") or [],
            subtasks=pending_clarification.get("subtasks") or [],
            missing_slots=missing_slots,
            clarification_required=True,
            clarification_question=(
                pending_clarification.get("clarification_question")
                or (
                    f"为了继续，我还需要确认：{'、'.join(missing_slots)}。"
                    if missing_slots
                    else "为了准确继续，请补充你希望查询的具体内容。"
                )
            ),
            risk_level=pending_clarification.get("risk_level") or "low",
            confidence=float(pending_clarification.get("confidence") or 0.7),
        )
    except (KeyError, TypeError, ValueError):
        return None


def normalize_resolution(
    message: str,
    resolution: IntentResolution,
    *,
    context_messages: list[dict[str, str]] | None = None,
) -> IntentResolution:
    """Apply deterministic safety and deduplication after untrusted model output."""
    expanded: list[IntentName] = []
    for intent in resolution.expanded_intents:
        if intent != resolution.primary_intent and intent not in expanded:
            expanded.append(intent)

    primary = resolution.primary_intent
    risk_level = resolution.risk_level
    rules_resolution = resolve_intent(message)
    intent_domain = resolution.intent_domain or _DOMAIN_BY_INTENT[primary]
    request_kind = resolution.request_kind
    requested_effect = resolution.requested_effect
    change_requests = list(resolution.change_requests)
    evidence_requirements = list(resolution.evidence_requirements)
    requested_output = resolution.requested_output
    rules_decision_overrides_non_decision = (
        rules_resolution.request_kind == "proposal_decision"
        and request_kind != "proposal_decision"
    )
    model_decision_actions = {
        str(change.value)
        for change in change_requests
        if change.field_path == "proposal.status"
        and change.value in {"confirm", "reject"}
    }
    rules_decision_fills_incomplete_action = (
        rules_resolution.request_kind == "proposal_decision"
        and request_kind == "proposal_decision"
        and len(model_decision_actions) != 1
    )
    if rules_resolution.request_kind == "proposal_decision" and (
        rules_decision_overrides_non_decision
        or rules_decision_fills_incomplete_action
    ):
        intent_domain = rules_resolution.intent_domain
        request_kind = rules_resolution.request_kind
        requested_effect = rules_resolution.requested_effect
        change_requests = list(rules_resolution.change_requests)
    elif (
        rules_resolution.request_kind == "mutation"
        and request_kind not in {"mutation", "proposal_decision"}
    ):
        # Rules may correct the read/write classification, but their regex
        # extraction is never promoted into authoritative write fields.
        intent_domain = rules_resolution.intent_domain
        request_kind = "mutation"
        requested_effect = rules_resolution.requested_effect
        change_requests = []
    elif (
        rules_resolution.request_kind == "assessment"
        and request_kind == "query"
    ):
        intent_domain = rules_resolution.intent_domain
        request_kind = "assessment"
        requested_effect = "read"
        change_requests = []
    elif (
        rules_resolution.request_kind == "generation"
        and request_kind in {"query", "assessment"}
    ):
        intent_domain = "nutrition"
        request_kind = "generation"
        requested_effect = "read"
        change_requests = []
        requested_output = "daily_meal_plan"
    elif request_kind in {"mutation", "proposal_decision"}:
        primary = _INTENT_BY_DOMAIN[intent_domain]
        expanded = []

    if request_kind == "proposal_decision":
        requested_effect = "decide"
    elif request_kind == "mutation":
        operations = {change.operation for change in change_requests}
        if len(operations) == 1:
            requested_effect = operations.pop()
    else:
        requested_effect = "read"
        change_requests = []
    if request_kind == "generation":
        requested_effect = "read"
        change_requests = []
        requested_output = "daily_meal_plan"
        evidence_requirements = list(_DAILY_MEAL_EVIDENCE)
    elif request_kind == "assessment" and intent_domain == "workout_plan":
        requested_output = "answer"
        evidence_requirements = [
            "active_plan",
            "profile_summary",
            "health_screening",
            "workout_progress",
        ]
    elif requested_output == "daily_meal_plan":
        requested_output = "answer"
        evidence_requirements = []
    else:
        allowed_evidence = {
            evidence
            for intent in [primary, *expanded]
            for evidence in _EVIDENCE_BY_INTENT[intent]
        }
        evidence_requirements = list(dict.fromkeys([
            *[
                evidence for evidence in evidence_requirements
                if evidence in allowed_evidence
            ],
            *[
                evidence
                for intent in [primary, *expanded]
                for evidence in _EVIDENCE_BY_INTENT[intent]
            ],
        ]))[:6]
    risk_rank = {"low": 0, "medium": 1, "high": 2}
    if risk_rank[rules_resolution.risk_level] > risk_rank[risk_level]:
        risk_level = rules_resolution.risk_level

    if (
        is_explicit_plan_adjustment_request(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        primary = "plan_query"
        expanded = []
    elif (
        _is_active_continuation_comparison(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        primary = "active_workout_query"
        expanded = ["next_workout_query"]
    elif (
        _is_plan_adjustment_assessment(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        primary = "plan_query"
        expanded = [
            "workout_progress_query",
            "workout_history_query",
        ]
        if _asks_personal_plan_fit(message):
            expanded.append("profile_query")
    elif rules_resolution.primary_intent == "health_query":
        if primary not in ("general_qa", "health_query") and primary not in expanded:
            expanded.insert(0, primary)
        primary = "health_query"

    if rules_resolution.primary_intent == primary:
        for intent in rules_resolution.expanded_intents:
            if intent != primary and intent not in expanded:
                expanded.append(intent)

    explicit_health_record = (
        request_kind == "mutation"
        and intent_domain in {"profile", "health"}
        and bool(change_requests)
        and all(
            change.resource in {"profile", "health"}
            and change.operation == "update"
            for change in change_requests
        )
    )
    if contains_health_red_flag(message):
        risk_level = "high"
        intent_domain = "health"
        if not explicit_health_record:
            request_kind = "query"
            requested_effect = "read"
            change_requests = []
        if primary != "health_query":
            if primary not in expanded:
                expanded.insert(0, primary)
            primary = "health_query"
    if (
        primary == "next_workout_query"
        and not any(keyword in message for keyword in _EXPLICIT_PLAN_KEYWORDS)
        and not any(
            reference.reference_type == "plan"
            for reference in resolution.references
        )
        and not any(
            keyword in resolution.resolved_query
            for keyword in _EXPLICIT_PLAN_KEYWORDS
        )
    ):
        expanded = [intent for intent in expanded if intent != "plan_query"]
    expanded = [intent for intent in expanded if intent != primary][:7]

    resolved_query = resolution.resolved_query.strip() or message.strip()
    subtasks = list(resolution.subtasks)
    if (
        _is_active_continuation_comparison(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        resolved_query = (
            "检查是否存在未完成的活动训练；若不存在，再查询下一练，"
            "判断现在应该继续上次训练还是开始下一练"
        )
        subtasks = ["检查活动训练", "必要时查询下一练"]
    elif request_kind == "mutation" and primary != "health_query":
        primary = (
            "general_qa"
            if intent_domain != "workout_plan"
            and contains_unsupported_write_request(message)
            else _INTENT_BY_DOMAIN[intent_domain]
        )
        expanded = []
        subtasks = (
            ["读取当前训练计划", _EXPLICIT_PROPOSAL_SUBTASK]
            if is_explicit_plan_adjustment_request(message)
            else ["读取当前训练计划", "校验变更并形成待确认提案"]
            if intent_domain == "workout_plan" and requested_effect == "update"
            else ["识别写入请求并进行能力校验"]
        )
    elif request_kind == "proposal_decision" and primary != "health_query":
        primary = _INTENT_BY_DOMAIN[intent_domain]
        expanded = []
        subtasks = ["定位当前会话唯一待确认提案", "安全执行提案决策"]
    elif request_kind == "generation" and primary != "health_query":
        primary = "nutrition_today_query"
        expanded = []
        subtasks = ["按需读取个性化证据", "生成并校验全天饮食方案"]
    elif (
        is_explicit_plan_adjustment_request(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        subtasks = [
            "读取当前训练计划",
            _EXPLICIT_PROPOSAL_SUBTASK,
        ]
    elif (
        _is_plan_adjustment_assessment(message)
        and rules_resolution.primary_intent != "health_query"
    ):
        subtasks = []
        if _asks_personal_plan_fit(message):
            subtasks.append("读取用户训练偏好")
        subtasks.extend([
            "读取当前训练计划",
            "读取近期训练进度，失败时改查历史记录",
            "评估并形成待确认的调整建议",
        ])
    if request_kind in {"mutation", "proposal_decision"}:
        semantic_validation = validate_semantic_changes(
            intent_domain=intent_domain,
            request_kind=request_kind,
            requested_effect=requested_effect,
            change_requests=change_requests,
        )
        missing_slots = list(semantic_validation.missing_slots)[:8]
        clarification_required = bool(missing_slots)
        clarification_question = semantic_validation.clarification_question
    elif request_kind == "generation":
        # The understanding model has not read private evidence and therefore
        # cannot authoritatively declare profile or health slots missing.
        # The Evidence Coordinator asks only after server-owned reads.
        missing_slots = []
        clarification_required = False
        clarification_question = None
    else:
        missing_slots = list(dict.fromkeys(
            item.strip() for item in resolution.missing_slots if item.strip()
        ))[:8]
        clarification_required = bool(
            resolution.clarification_required or missing_slots
        )
        clarification_question = resolution.clarification_question
        if clarification_required and not clarification_question:
            clarification_question = (
                f"为了继续，我还需要确认：{'、'.join(missing_slots)}。"
                if missing_slots
                else "为了准确继续，请补充你希望查询的具体内容。"
            )
        if not clarification_required:
            clarification_question = None

    references = list(resolution.references)
    if not references and context_messages:
        expression = next(
            (
                marker for marker in _CONTEXT_REFERENCE_MARKERS
                if marker in message
            ),
            None,
        )
        last_context = context_messages[-1]
        if expression and last_context.get("role") in {"user", "assistant"}:
            reference_type: Literal[
                "message",
                "exercise",
                "plan",
                "time_range",
                "metric",
                "other",
            ] = (
                "exercise" if "动作" in message
                else "plan" if "计划" in message
                else "message"
            )
            references.append(ResolvedReference(
                expression=expression,
                resolved_value=str(last_context.get("content") or "")[:500],
                reference_type=reference_type,
                source="recent_conversation",
            ))

    return IntentResolution.model_validate({
        **resolution.model_dump(),
        "primary_intent": primary,
        "intent_domain": intent_domain,
        "request_kind": request_kind,
        "requested_effect": requested_effect,
        "change_requests": change_requests,
        "evidence_requirements": evidence_requirements[:6],
        "requested_output": requested_output,
        "expanded_intents": expanded,
        "risk_level": risk_level,
        "resolved_query": resolved_query,
        "missing_slots": missing_slots,
        "clarification_required": clarification_required,
        "clarification_question": clarification_question,
        "references": references[:12],
        "subtasks": subtasks,
    })


INTENT_TOOL_ALLOWLIST: dict[IntentName, tuple[str, ...]] = {
    "general_qa": (),
    "profile_query": ("profile.get_summary",),
    "health_query": ("health.get_screening_summary",),
    "plan_query": ("plan.get_active",),
    "next_workout_query": ("workout.get_next",),
    "active_workout_query": ("workout.get_active_session",),
    "workout_history_query": ("workout.list_history",),
    "workout_progress_query": ("workout.get_progress",),
    "weight_history_query": ("weight.list_history",),
    "nutrition_today_query": ("nutrition.get_today",),
    "nutrition_history_query": ("nutrition.list_history",),
    "food_search_query": ("food.search",),
}

EVIDENCE_TOOL_ALLOWLIST: dict[EvidenceRequirement, tuple[str, ...]] = {
    "profile_summary": ("profile.get_summary",),
    "health_screening": ("health.get_screening_summary",),
    "weight_history": ("weight.list_history",),
    "active_plan": ("plan.get_active",),
    "workout_progress": ("workout.get_progress",),
    "workout_daily_context": ("workout.get_daily_context",),
    "nutrition_today": ("nutrition.get_today",),
    "nutrition_history": ("nutrition.list_history",),
    "nutrition_recent_context": ("nutrition.get_recent_context",),
    "food_catalog": ("food.list_candidates",),
}

MAX_ROUTED_TOOLS = 4


def route_tools(resolution: IntentResolution) -> list[str]:
    """Return a stable, deduplicated allowlist. Unknown tools can never be added."""
    if resolution.clarification_required or resolution.risk_level == "high":
        return []
    if resolution.request_kind == "proposal_decision":
        return []
    if resolution.request_kind == "generation":
        return []
    if resolution.request_kind == "mutation":
        return (
            ["plan.get_active"]
            if resolution.intent_domain == "workout_plan"
            and resolution.requested_effect == "update"
            and bool(resolution.change_requests)
            else []
        )
    routed: list[str] = []
    tool_groups = (
        [EVIDENCE_TOOL_ALLOWLIST[item] for item in resolution.evidence_requirements]
        if resolution.evidence_requirements
        and resolution.request_kind == "assessment"
        else [
            INTENT_TOOL_ALLOWLIST[intent]
            for intent in [resolution.primary_intent, *resolution.expanded_intents]
        ]
    )
    for tool_group in tool_groups:
        for tool_id in tool_group:
            if tool_id not in routed:
                routed.append(tool_id)
                if len(routed) >= MAX_ROUTED_TOOLS:
                    return routed
    return routed
