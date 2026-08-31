import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.agent_intent import (
    IntentResolution,
    ResolvedReference,
    resolve_intent,
    route_tools,
)
from app.services.agent_intent_model import (
    IntentStructuredOutputError,
    _intent_attempt_timeout_seconds,
    _structured_error_category,
    resolve_intent_with_fallback,
)


class IntentModelTimeoutError(RuntimeError):
    pass


class SafeFakeValidationError(Exception):
    def errors(self):
        return [{
            "type": "literal_error",
            "loc": ("expanded_intents", 0),
            "input": "must-never-appear-in-category",
        }]


@pytest.fixture(autouse=True)
def disable_rules_first_for_model_contract_tests(monkeypatch):
    """Most tests in this module explicitly exercise the model branch."""
    monkeypatch.setattr(settings, "AGENT_RULES_FIRST_ENABLED", False)


@pytest.mark.asyncio
async def test_structured_model_resolution_is_normalized_and_keeps_whitelist():
    candidate = IntentResolution(
        primary_intent="next_workout_query",
        expanded_intents=[
            "next_workout_query",
            "plan_query",
            "plan_query",
        ],
        subtasks=["读取下一练", "核对计划"],
        confidence=0.93,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "结合当前计划告诉我下一练",
            use_model=True,
        )

    assert outcome.source == "model"
    assert outcome.attempt_count == 1
    assert outcome.latency_ms >= 0
    assert len(outcome.attempt_timings) == 1
    assert outcome.attempt_timings[0].status == "success"
    assert outcome.resolution.expanded_intents == ["plan_query"]
    assert route_tools(outcome.resolution) == [
        "workout.get_next",
        "plan.get_active",
    ]
    invoked.assert_awaited_once()


@pytest.mark.asyncio
async def test_structured_model_gets_one_repair_attempt():
    repaired = IntentResolution(
        primary_intent="workout_progress_query",
        subtasks=["读取训练进度"],
        confidence=0.88,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=[ValueError("bad schema"), repaired]),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "最近训练进度怎么样",
            use_model=True,
        )

    assert outcome.source == "model"
    assert outcome.attempt_count == 2
    assert outcome.error_category == "ValueError"
    assert [item.status for item in outcome.attempt_timings] == [
        "error",
        "success",
    ]
    assert outcome.attempt_timings[0].error_category == "ValueError"
    assert invoked.await_count == 2


@pytest.mark.asyncio
async def test_rules_resolution_reports_total_latency_without_model_attempts():
    outcome = await resolve_intent_with_fallback(
        "查看我的训练历史",
        use_model=False,
    )

    assert outcome.source == "rules"
    assert outcome.latency_ms >= 0
    assert outcome.attempt_timings == ()


@pytest.mark.asyncio
async def test_structured_model_falls_back_after_two_failures():
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=ValueError("bad schema")),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "查看我的训练历史",
            use_model=True,
        )

    assert outcome.source == "rules"
    assert outcome.fallback_reason == "schema_validation_failed"
    assert outcome.error_category == "ValueError"
    assert outcome.resolution.primary_intent == "workout_history_query"
    assert invoked.await_count == 2


@pytest.mark.asyncio
async def test_provider_failure_falls_back_without_wasteful_repair():
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "我的下一练是什么",
            use_model=True,
        )

    assert outcome.source == "rules"
    assert outcome.attempt_count == 1
    assert outcome.fallback_reason == "model_unavailable"
    invoked.assert_awaited_once()


@pytest.mark.asyncio
async def test_high_confidence_rules_first_skips_intent_model():
    with (
        patch.object(settings, "AGENT_RULES_FIRST_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "我昨天那次训练没做完，现在应该接着练还是开始下一练？",
            use_model=True,
        )

    assert outcome.source == "rules"
    assert outcome.attempt_count == 0
    assert outcome.fallback_reason == "high_confidence_rules_first"
    assert route_tools(outcome.resolution) == [
        "workout.get_active_session",
        "workout.get_next",
    ]
    assert outcome.resolution.subtasks == [
        "检查活动训练",
        "必要时查询下一练",
    ]
    invoked.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_next_workout_composite_uses_rules_first():
    with (
        patch.object(settings, "AGENT_RULES_FIRST_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "我膝盖最近不舒服，结合健康筛查和下一练，告诉我哪些内容需要避开。",
            use_model=True,
        )

    assert outcome.source == "rules"
    assert outcome.fallback_reason == "high_confidence_rules_first"
    assert outcome.attempt_count == 0
    assert outcome.resolution.primary_intent == "health_query"
    assert outcome.resolution.expanded_intents == ["next_workout_query"]
    assert outcome.resolution.risk_level == "medium"
    assert route_tools(outcome.resolution) == [
        "health.get_screening_summary",
        "workout.get_next",
    ]
    invoked.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_dependent_high_confidence_query_still_uses_model():
    candidate = IntentResolution(
        primary_intent="plan_query",
        resolved_query="查询上一轮提到的当前训练计划",
        subtasks=["读取当前计划"],
        confidence=0.91,
    )
    with (
        patch.object(settings, "AGENT_RULES_FIRST_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "这个训练计划是什么？",
            context_messages=[{
                "role": "assistant",
                "content": "刚才展示的是你的当前训练计划。",
            }],
            use_model=True,
        )

    assert outcome.source == "model"
    invoked.assert_awaited_once()


@pytest.mark.asyncio
async def test_intent_timeout_gets_one_bounded_retry_and_can_recover():
    recovered = IntentResolution(
        primary_intent="active_workout_query",
        expanded_intents=["next_workout_query"],
        subtasks=["检查活动训练", "必要时查询下一练"],
        confidence=0.88,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=[
                IntentModelTimeoutError("timeout"),
                recovered,
            ]),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "昨天没做完，现在接着练还是开始下一练？",
            use_model=True,
        )

    assert outcome.source == "model"
    assert outcome.attempt_count == 2
    assert outcome.error_category == "IntentModelTimeoutError"
    assert invoked.await_count == 2


@pytest.mark.asyncio
async def test_intent_timeout_stops_after_exactly_two_attempts():
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=IntentModelTimeoutError("timeout")),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "昨天没做完，现在接着练还是开始下一练？",
            use_model=True,
        )

    assert outcome.source == "rules"
    assert outcome.attempt_count == 2
    assert outcome.fallback_reason == "model_timeout"
    assert outcome.error_category == "IntentModelTimeoutError"
    assert invoked.await_count == 2


def test_intent_attempt_timeout_reserves_total_retry_budget():
    with (
        patch.object(settings, "AGENT_INTENT_TIMEOUT_SECONDS", 6.0),
        patch.object(
            settings,
            "AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS",
            2.0,
        ),
    ):
        first = _intent_attempt_timeout_seconds(
            attempt=1,
            remaining_seconds=10.0,
        )
        second = _intent_attempt_timeout_seconds(
            attempt=2,
            remaining_seconds=4.0,
        )

    assert first == 6.0
    assert second == 4.0


@pytest.mark.asyncio
async def test_intent_retries_stay_inside_total_deadline():
    calls = 0

    async def slow_intent(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        raise AssertionError("deadline should cancel the attempt")

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "AGENT_INTENT_TIMEOUT_SECONDS", 0.03),
        patch.object(settings, "AGENT_INTENT_TOTAL_TIMEOUT_SECONDS", 0.08),
        patch.object(
            settings,
            "AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS",
            0.01,
        ),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=slow_intent,
        ),
    ):
        started = time.monotonic()
        outcome = await resolve_intent_with_fallback(
            "帮我比较一个不明确的训练问题",
            use_model=True,
        )
        elapsed = time.monotonic() - started

    assert outcome.source == "rules"
    assert outcome.fallback_reason == "model_timeout"
    assert calls == 2
    assert elapsed < 0.2


def test_structured_error_category_exposes_path_without_raw_value():
    category = _structured_error_category(SafeFakeValidationError())

    assert category == (
        "SafeFakeValidationError>SafeFakeValidationError:"
        "literal_error@expanded_intents.0"
    )
    assert "must-never-appear" not in category


@pytest.mark.asyncio
async def test_structured_error_category_is_exposed_on_fallback():
    category = (
        "ValidationError>ValidationError:"
        "literal_error@expanded_intents.0"
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=IntentStructuredOutputError(category)),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "查看我的训练历史",
            use_model=True,
        )

    assert outcome.fallback_reason == "schema_validation_failed"
    assert outcome.error_category == category


def test_health_red_flag_overrides_tools_without_model():
    resolution = resolve_intent("训练时突然呼吸困难，还能继续吗？")

    assert resolution.primary_intent == "health_query"
    assert resolution.risk_level == "high"
    assert route_tools(resolution) == []


@pytest.mark.asyncio
async def test_deterministic_health_overlay_corrects_unsafe_model_candidate():
    candidate = IntentResolution(
        primary_intent="general_qa",
        expanded_intents=["health_query"],
        subtasks=["回答问题"],
        risk_level="low",
        confidence=0.7,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "我现在胸痛，还能继续训练吗？",
            use_model=True,
        )

    assert outcome.resolution.primary_intent == "health_query"
    assert outcome.resolution.expanded_intents == []
    assert outcome.resolution.risk_level == "high"
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_explicit_rule_intents_fill_model_expansion_gaps():
    candidate = IntentResolution(
        primary_intent="plan_query",
        expanded_intents=[],
        subtasks=["读取计划"],
        confidence=0.86,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "结合我的资料，看看当前计划是什么",
            use_model=True,
        )

    assert outcome.resolution.primary_intent == "plan_query"
    assert outcome.resolution.expanded_intents == ["profile_query"]
    assert route_tools(outcome.resolution) == [
        "plan.get_active",
        "profile.get_summary",
    ]


@pytest.mark.asyncio
async def test_unsupported_write_request_never_routes_a_read_tool():
    candidate = IntentResolution(
        primary_intent="active_workout_query",
        subtasks=["完成训练"],
        confidence=0.8,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "替我完成训练并保存",
            use_model=True,
        )

    assert outcome.resolution.primary_intent == "general_qa"
    assert outcome.resolution.expanded_intents == []
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_explicit_red_flag_health_update_can_be_structured_but_stays_high_risk():
    candidate = IntentResolution.model_validate({
        "primary_intent": "health_query",
        "intent_domain": "health",
        "request_kind": "mutation",
        "requested_effect": "update",
        "change_requests": [{
            "resource": "health",
            "operation": "update",
            "field_path": "health.chronic_conditions",
            "target_reference": None,
            "value": ["心血管疾病"],
            "preserve_unspecified": True,
        }],
        "resolved_query": "将慢性情况更新为心血管疾病，并优先处理当前胸痛",
        "references": [],
        "expanded_intents": [],
        "subtasks": ["记录健康资料", "显示紧急安全提示"],
        "missing_slots": [],
        "clarification_required": False,
        "clarification_question": None,
        "risk_level": "high",
        "confidence": 0.98,
    })
    with (
        patch.object(settings, "AGENT_RULES_FIRST_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "请把我的慢性情况更新为心血管疾病，我现在胸痛",
            use_model=True,
        )

    invoked.assert_awaited_once()
    assert outcome.resolution.primary_intent == "health_query"
    assert outcome.resolution.intent_domain == "health"
    assert outcome.resolution.request_kind == "mutation"
    assert outcome.resolution.requested_effect == "update"
    assert outcome.resolution.risk_level == "high"
    assert len(outcome.resolution.change_requests) == 1
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_plan_mutation_uses_model_first_and_safe_structured_fallback():
    with (
        patch.object(settings, "AGENT_RULES_FIRST_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ) as invoked,
    ):
        outcome = await resolve_intent_with_fallback(
            "把我的训练计划调整为每周 3 天",
            use_model=True,
        )

    assert invoked.await_count == 1
    assert outcome.source == "rules"
    assert outcome.resolution.request_kind == "mutation"
    assert outcome.resolution.change_requests[0].field_path == (
        "schedule.days_per_week"
    )
    assert outcome.resolution.change_requests[0].value == 3
    assert route_tools(outcome.resolution) == ["plan.get_active"]


@pytest.mark.asyncio
async def test_server_overlay_blocks_model_from_downgrading_mutation_to_query():
    unsafe_candidate = IntentResolution(
        primary_intent="plan_query",
        intent_domain="workout_plan",
        request_kind="query",
        requested_effect="read",
        subtasks=["回答计划问题"],
        confidence=0.95,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=unsafe_candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "把我的训练计划调整为每周 3 天",
            use_model=True,
        )

    assert outcome.resolution.request_kind == "mutation"
    assert outcome.resolution.requested_effect == "update"
    assert outcome.resolution.change_requests[0].value == 3
    assert route_tools(outcome.resolution) == ["plan.get_active"]


@pytest.mark.asyncio
async def test_next_workout_removes_redundant_plan_tool_without_explicit_plan_request():
    candidate = IntentResolution(
        primary_intent="next_workout_query",
        expanded_intents=["plan_query"],
        subtasks=["读取下一练"],
        confidence=0.91,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "我下一练做什么？",
            use_model=True,
        )

    assert outcome.resolution.expanded_intents == []
    assert route_tools(outcome.resolution) == ["workout.get_next"]


@pytest.mark.asyncio
async def test_active_continuation_overlay_removes_model_overexpansion():
    candidate = IntentResolution(
        primary_intent="next_workout_query",
        resolved_query="查询昨天记录并核对计划后判断下一练",
        expanded_intents=[
            "workout_history_query",
            "plan_query",
            "active_workout_query",
        ],
        subtasks=["查询历史", "读取计划", "查询下一练"],
        confidence=0.84,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "昨天那次训练没做完，现在应该接着练还是开始下一练？",
            use_model=True,
        )

    assert outcome.source == "model"
    assert outcome.resolution.primary_intent == "active_workout_query"
    assert outcome.resolution.expanded_intents == ["next_workout_query"]
    assert outcome.resolution.subtasks == [
        "检查活动训练",
        "必要时查询下一练",
    ]
    assert route_tools(outcome.resolution) == [
        "workout.get_active_session",
        "workout.get_next",
    ]


@pytest.mark.asyncio
async def test_active_continuation_overlay_never_overrides_health_red_flag():
    candidate = IntentResolution(
        primary_intent="next_workout_query",
        expanded_intents=["active_workout_query"],
        subtasks=["继续训练"],
        confidence=0.8,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "昨天没做完，我现在胸痛，是接着练还是开始下一练？",
            use_model=True,
        )

    assert outcome.resolution.primary_intent == "health_query"
    assert outcome.resolution.risk_level == "high"
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_plan_fit_overlay_restores_profile_and_history_candidates():
    candidate = IntentResolution(
        primary_intent="plan_query",
        expanded_intents=["workout_progress_query"],
        subtasks=["读取计划", "读取进度"],
        confidence=0.88,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。",
            use_model=True,
        )

    assert outcome.resolution.expanded_intents == [
        "workout_progress_query",
        "workout_history_query",
        "profile_query",
    ]
    assert outcome.resolution.subtasks == [
        "读取用户训练偏好",
        "读取当前训练计划",
        "读取近期训练进度，失败时改查历史记录",
        "评估并形成待确认的调整建议",
    ]
    assert route_tools(outcome.resolution) == [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
        "profile.get_summary",
    ]


@pytest.mark.asyncio
async def test_explicit_proposal_overlay_overrides_model_general_answer():
    candidate = IntentResolution(
        primary_intent="general_qa",
        resolved_query="解释如何手动延长计划",
        subtasks=["回答一般问题"],
        confidence=0.91,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "请把当前训练计划周期从6周延长到8周，其他内容保持不变，"
            "并生成待确认提案。",
            use_model=True,
        )

    assert outcome.resolution.primary_intent == "plan_query"
    assert outcome.resolution.expanded_intents == []
    assert outcome.resolution.subtasks == [
        "读取当前训练计划",
        "根据用户明确范围形成待确认的训练计划调整提案",
    ]
    assert route_tools(outcome.resolution) == ["plan.get_active"]


@pytest.mark.asyncio
async def test_affirmative_followup_inherits_the_last_assistant_offer():
    outcome = await resolve_intent_with_fallback(
        "需要",
        context_messages=[
            {"role": "user", "content": "我最近有点不知道该怎么练"},
            {"role": "assistant", "content": "需要我帮你查下次该练什么吗？"},
        ],
        use_model=False,
    )

    assert outcome.source == "rules"
    assert outcome.fallback_reason == "contextual_followup"
    assert outcome.resolution.primary_intent == "next_workout_query"
    assert route_tools(outcome.resolution) == ["workout.get_next"]


@pytest.mark.asyncio
async def test_affirmative_followup_does_not_infer_from_a_non_offer():
    outcome = await resolve_intent_with_fallback(
        "好的",
        context_messages=[
            {"role": "assistant", "content": "规律训练和充分恢复都很重要。"},
        ],
        use_model=False,
    )

    assert outcome.resolution.primary_intent == "general_qa"
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_model_result_preserves_reference_expansion_and_decomposition():
    candidate = IntentResolution(
        primary_intent="next_workout_query",
        resolved_query="结合当前训练计划和最近进度，查询我下一练应该做什么",
        references=[ResolvedReference(
            expression="这个计划",
            resolved_value="上一轮提到的当前训练计划",
            reference_type="plan",
            source="recent_conversation",
        )],
        expanded_intents=["plan_query", "workout_progress_query"],
        subtasks=["读取下一练", "核对当前计划", "分析最近训练进度"],
        confidence=0.91,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "结合这个计划和最近表现再说一下",
            context_messages=[
                {"role": "assistant", "content": "刚才展示的是你的当前训练计划。"},
            ],
            use_model=True,
        )

    assert outcome.resolution.resolved_query.startswith("结合当前训练计划")
    assert outcome.resolution.references[0].reference_type == "plan"
    assert outcome.resolution.subtasks == [
        "读取下一练",
        "核对当前计划",
        "分析最近训练进度",
    ]
    assert route_tools(outcome.resolution) == [
        "workout.get_next",
        "plan.get_active",
        "workout.get_progress",
    ]


@pytest.mark.asyncio
async def test_single_pending_clarification_slot_is_filled_without_model():
    outcome = await resolve_intent_with_fallback(
        "最近四周",
        pending_clarification={
            "resolved_query": "比较我的训练历史",
            "primary_intent": "workout_history_query",
            "expanded_intents": [],
            "subtasks": ["读取训练历史", "比较训练表现"],
            "missing_slots": ["要比较的时间范围"],
            "clarification_question": "你想比较哪个时间范围？",
            "confidence": 0.72,
        },
        use_model=False,
    )

    assert outcome.fallback_reason == "clarification_filled"
    assert outcome.resolution.clarification_required is False
    assert outcome.resolution.missing_slots == []
    assert "最近四周" in outcome.resolution.resolved_query
    assert outcome.resolution.references[-1].source == "pending_clarification"
    assert route_tools(outcome.resolution) == ["workout.list_history"]


@pytest.mark.asyncio
async def test_explicit_new_query_is_not_captured_by_pending_clarification():
    outcome = await resolve_intent_with_fallback(
        "我的下一练是什么？",
        pending_clarification={
            "resolved_query": "比较我的训练历史",
            "primary_intent": "workout_history_query",
            "missing_slots": ["要比较的时间范围"],
            "clarification_question": "你想比较哪个时间范围？",
        },
        use_model=False,
    )

    assert outcome.resolution.primary_intent == "next_workout_query"
    assert outcome.resolution.clarification_required is False
    assert route_tools(outcome.resolution) == ["workout.get_next"]


@pytest.mark.asyncio
async def test_multi_slot_pending_state_reasks_when_model_is_unavailable():
    outcome = await resolve_intent_with_fallback(
        "我不确定",
        pending_clarification={
            "resolved_query": "比较训练记录",
            "primary_intent": "workout_history_query",
            "missing_slots": ["时间范围", "比较指标"],
            "clarification_question": "请告诉我要比较的时间范围和指标。",
        },
        use_model=False,
    )

    assert outcome.resolution.clarification_required is True
    assert outcome.resolution.missing_slots == ["时间范围", "比较指标"]
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_invalid_time_slot_answer_keeps_clarification_open():
    outcome = await resolve_intent_with_fallback(
        "不知道",
        pending_clarification={
            "resolved_query": "比较我的训练历史",
            "primary_intent": "workout_history_query",
            "missing_slots": ["要比较的时间范围"],
            "clarification_question": "你想比较哪个时间范围？",
        },
        use_model=False,
    )

    assert outcome.resolution.clarification_required is True
    assert outcome.resolution.clarification_question == "你想比较哪个时间范围？"
    assert route_tools(outcome.resolution) == []


@pytest.mark.asyncio
async def test_context_reference_is_audited_when_model_omits_reference_details():
    candidate = IntentResolution(
        primary_intent="workout_progress_query",
        resolved_query="查询上周的训练进度",
        subtasks=["查询上周训练进度"],
        confidence=0.88,
    )
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=candidate),
        ),
    ):
        outcome = await resolve_intent_with_fallback(
            "那上周呢？",
            context_messages=[{
                "role": "assistant",
                "content": "你最近八周完成了12次训练。",
            }],
            use_model=True,
        )

    assert outcome.resolution.references[0].expression == "那"
    assert outcome.resolution.references[0].source == "recent_conversation"
