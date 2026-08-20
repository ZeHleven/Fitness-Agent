from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.agent_intent import (
    IntentResolution,
    ResolvedReference,
    resolve_intent,
    route_tools,
)
from app.services.agent_intent_model import resolve_intent_with_fallback


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
    assert invoked.await_count == 2


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
