from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IntentName = Literal[
    "general_qa",
    "profile_query",
    "health_query",
    "plan_query",
    "next_workout_query",
    "active_workout_query",
    "workout_history_query",
    "workout_progress_query",
]


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
    resolved_query: str = Field(default="", max_length=4000)
    references: list[ResolvedReference] = Field(default_factory=list, max_length=12)
    expanded_intents: list[IntentName] = Field(default_factory=list, max_length=7)
    subtasks: list[str] = Field(default_factory=list, max_length=8)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    clarification_required: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    risk_level: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(ge=0, le=1)


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
}

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
    return any(keyword in normalized for keyword in _UNSUPPORTED_WRITE_KEYWORDS)


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

    if contains_unsupported_write_request(message) and "health_query" not in matched:
        return IntentResolution(
            primary_intent="general_qa",
            resolved_query=message.strip(),
            expanded_intents=[],
            subtasks=["说明当前不能直接执行写操作"],
            confidence=0.95,
        )

    if contains_health_red_flag(message) and "health_query" not in matched:
        matched.insert(0, "health_query")

    if (
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
            resolved_query=message.strip(),
            expanded_intents=[],
            subtasks=["回答一般健身问题"],
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
    return IntentResolution(
        primary_intent=primary,
        resolved_query=resolved_query,
        expanded_intents=matched[1:],
        subtasks=subtasks,
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
    risk_rank = {"low": 0, "medium": 1, "high": 2}
    if risk_rank[rules_resolution.risk_level] > risk_rank[risk_level]:
        risk_level = rules_resolution.risk_level

    if (
        contains_unsupported_write_request(message)
        and rules_resolution.primary_intent == "general_qa"
        and rules_resolution.risk_level == "low"
    ):
        primary = "general_qa"
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

    if contains_health_red_flag(message):
        risk_level = "high"
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
}

MAX_ROUTED_TOOLS = 4


def route_tools(resolution: IntentResolution) -> list[str]:
    """Return a stable, deduplicated allowlist. Unknown tools can never be added."""
    if resolution.clarification_required or resolution.risk_level == "high":
        return []
    routed: list[str] = []
    for intent in [resolution.primary_intent, *resolution.expanded_intents]:
        for tool_id in INTENT_TOOL_ALLOWLIST[intent]:
            if tool_id not in routed:
                routed.append(tool_id)
                if len(routed) >= MAX_ROUTED_TOOLS:
                    return routed
    return routed
