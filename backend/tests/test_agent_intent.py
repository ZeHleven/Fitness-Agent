import pytest

from app.services.agent_intent import (
    parse_explicit_plan_adjustment_command,
    resolve_intent,
    route_tools,
)


def test_general_question_has_no_tools():
    resolution = resolve_intent("深蹲时应该怎么呼吸？")

    assert resolution.primary_intent == "general_qa"
    assert route_tools(resolution) == []


def test_composite_intent_expands_and_routes_a_union_of_read_tools():
    resolution = resolve_intent("我的膝盖疼，最近训练进度怎么样？")

    assert resolution.primary_intent == "health_query"
    assert "workout_progress_query" in resolution.expanded_intents
    assert route_tools(resolution) == [
        "health.get_screening_summary",
        "workout.get_progress",
    ]
    assert resolution.risk_level == "medium"


def test_plan_and_next_workout_have_non_overlapping_routes():
    assert route_tools(resolve_intent("查看我的当前训练计划")) == [
        "plan.get_active"
    ]
    assert route_tools(resolve_intent("我下一练做什么？")) == [
        "workout.get_next"
    ]


def test_router_never_exposes_write_tools():
    messages = [
        "我的资料是什么？",
        "完成训练并替我保存",
        "查看训练历史和训练进度",
    ]

    routed = {
        tool_id
        for message in messages
        for tool_id in route_tools(resolve_intent(message))
    }

    assert all(not item.startswith("write.") for item in routed)
    assert "workout.record_set" not in routed
    assert "workout.complete" not in routed


@pytest.mark.parametrize(
    "message",
    [
        "昨天那次没做完，现在接着练还是开始下一练？",
        "我有未完成训练，还能直接开始下一练吗？",
        "我想继续上次的训练，然后再看下一练。",
    ],
)
def test_unfinished_workout_fallback_routes_active_session(message):
    resolution = resolve_intent(message)

    assert "active_workout_query" in {
        resolution.primary_intent,
        *resolution.expanded_intents,
    }
    assert "workout.get_active_session" in route_tools(resolution)


def test_unfinished_workout_counterfactual_gets_both_dynamic_candidates():
    resolution = resolve_intent(
        "我昨天那次训练没做完，现在应该接着练还是开始下一练？"
    )

    assert resolution.primary_intent == "active_workout_query"
    assert resolution.expanded_intents == ["next_workout_query"]
    assert route_tools(resolution) == [
        "workout.get_active_session",
        "workout.get_next",
    ]


def test_plan_adjustment_fallback_includes_progress_failure_alternative():
    resolution = resolve_intent(
        "结合最近四周训练情况，判断当前计划是否需要调整。"
    )

    assert resolution.primary_intent == "plan_query"
    assert resolution.expanded_intents == [
        "workout_progress_query",
        "workout_history_query",
    ]
    assert route_tools(resolution) == [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
    ]


def test_personal_plan_fit_fallback_also_includes_profile():
    resolution = resolve_intent(
        "结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。"
    )

    assert resolution.expanded_intents == [
        "workout_progress_query",
        "workout_history_query",
        "profile_query",
    ]
    assert route_tools(resolution) == [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
        "profile.get_summary",
    ]


def test_explicit_plan_adjustment_proposal_routes_authoritative_plan_read():
    resolution = resolve_intent(
        "请把当前训练计划周期从6周延长到8周，其他内容保持不变，"
        "并生成待确认提案。"
    )

    assert resolution.primary_intent == "plan_query"
    assert resolution.expanded_intents == []
    assert resolution.subtasks == [
        "读取当前训练计划",
        "根据用户明确范围形成待确认的训练计划调整提案",
    ]
    assert route_tools(resolution) == ["plan.get_active"]


@pytest.mark.parametrize(
    ("message", "expected_before", "expected_after"),
    [
        (
            "请把当前训练计划周期从6周延长到8周，"
            "其他内容保持不变，并生成待确认提案。",
            6,
            8,
        ),
        (
            "将我的训练计划周期从8周缩短至6周，"
            "其余内容完全不变，生成调整提案",
            8,
            6,
        ),
        (
            "请将当前训练计划的周期从6周改为8周，"
            "其它内容不变，请生成待确认提案",
            6,
            8,
        ),
    ],
)
def test_explicit_duration_adjustment_parser_returns_typed_exact_command(
    message,
    expected_before,
    expected_after,
):
    command = parse_explicit_plan_adjustment_command(message)

    assert command is not None
    assert command.operation == "set_duration_weeks"
    assert command.expected_duration_weeks == expected_before
    assert command.target_duration_weeks == expected_after
    assert command.preserve_other_fields is True


@pytest.mark.parametrize(
    "message",
    [
        "请调整当前训练计划并生成待确认提案。",
        "请把当前训练计划周期延长到8周，并生成待确认提案。",
        "请把当前训练计划周期从6周延长到8周。",
        "请把当前训练计划周期从6周延长到8周，并减少训练组数，生成提案。",
        "请把当前训练计划周期从8周延长到6周，其他内容保持不变，并生成提案。",
        "请把当前训练计划周期从1周改为8周，其他内容保持不变，并生成提案。",
    ],
)
def test_explicit_duration_adjustment_parser_rejects_ambiguous_or_unsafe_scope(
    message,
):
    assert parse_explicit_plan_adjustment_command(message) is None


def test_explicit_plan_adjustment_never_overrides_health_red_flag():
    resolution = resolve_intent(
        "我现在胸痛，但请把当前训练计划周期从6周延长到8周，"
        "其他内容保持不变，并生成待确认提案。"
    )

    assert resolution.primary_intent == "health_query"
    assert resolution.risk_level == "high"
    assert route_tools(resolution) == []


def test_plain_plan_question_does_not_become_a_proposal_candidate():
    resolution = resolve_intent("训练计划通常应该怎样延长周期？")

    assert resolution.primary_intent == "plan_query"
    assert resolution.subtasks == ["查询并回答：plan_query"]
    assert route_tools(resolution) == ["plan.get_active"]


@pytest.mark.parametrize(
    "message",
    [
        "把我的训练计划调整为每周 3 天",
        "请将当前计划改成一周练三天",
        "我的计划每周训练天数设为3天",
        "可以把我的计划改成每周三天吗？",
    ],
)
def test_plan_frequency_paraphrases_share_structured_mutation_semantics(message):
    resolution = resolve_intent(message)

    assert resolution.intent_domain == "workout_plan"
    assert resolution.request_kind == "mutation"
    assert resolution.requested_effect == "update"
    assert [item.model_dump() for item in resolution.change_requests] == [{
        "resource": "workout_plan",
        "operation": "update",
        "field_path": "schedule.days_per_week",
        "target_reference": None,
        "value": 3,
        "preserve_unspecified": True,
    }]
    assert route_tools(resolution) == ["plan.get_active"]


def test_read_advice_and_mutation_frequency_requests_are_not_conflated():
    read = resolve_intent("我每周练几天？")
    advice = resolve_intent("怎样安排三天训练？")
    mutation = resolve_intent("把训练计划改成每周三天")

    assert (read.request_kind, read.requested_effect) == ("query", "read")
    assert route_tools(read) == ["plan.get_active"]
    assert (advice.request_kind, advice.requested_effect) == ("query", "read")
    assert advice.change_requests == []
    assert (mutation.request_kind, mutation.requested_effect) == (
        "mutation",
        "update",
    )


def test_qualitative_plan_mutation_requires_a_concrete_target():
    resolution = resolve_intent("把训练频率降低一点")

    assert resolution.request_kind == "mutation"
    assert resolution.change_requests[0].field_path == "schedule.days_per_week"
    assert resolution.change_requests[0].value is None
    assert resolution.clarification_required is True
    assert "目标值" in resolution.missing_slots[0]
    assert route_tools(resolution) == []


@pytest.mark.parametrize(
    ("message", "field_path", "target_reference", "value"),
    [
        ("把计划周期改成8周", "schedule.duration_weeks", None, 8),
        ("把高脚杯深蹲组数改成3组", "exercise.sets", "高脚杯深蹲", 3),
        ("将深蹲每组次数调整为10-12次", "exercise.reps", "深蹲", "10-12"),
        ("把深蹲休息时间改成90秒", "exercise.rest_seconds", "深蹲", 90),
        (
            "把深蹲重量改为30公斤",
            "exercise.recommended_weight_kg",
            "深蹲",
            30.0,
        ),
    ],
)
def test_supported_plan_targets_are_extracted_as_structured_changes(
    message,
    field_path,
    target_reference,
    value,
):
    resolution = resolve_intent(message)

    assert resolution.request_kind == "mutation"
    assert resolution.requested_effect == "update"
    assert resolution.clarification_required is False
    assert len(resolution.change_requests) == 1
    change = resolution.change_requests[0]
    assert change.field_path == field_path
    assert change.target_reference == target_reference
    assert change.value == value
    assert route_tools(resolution) == ["plan.get_active"]


def test_unsupported_write_is_recognized_without_becoming_a_read():
    resolution = resolve_intent("删除这个训练计划")

    assert resolution.intent_domain == "workout_plan"
    assert resolution.request_kind == "mutation"
    assert resolution.requested_effect == "delete"
    assert resolution.clarification_required is False
    assert route_tools(resolution) == []


@pytest.mark.parametrize(
    ("message", "domain", "effect", "clarification"),
    [
        ("更新我的个人资料", "profile", "update", True),
        ("新增一条饮食记录", "nutrition", "create", True),
        ("删除最近的训练记录", "workout_history", "delete", False),
    ],
)
def test_other_domain_crud_without_target_is_recognized_and_clarified(
    message, domain, effect, clarification
):
    resolution = resolve_intent(message)

    assert resolution.intent_domain == domain
    assert resolution.request_kind == "mutation"
    assert resolution.requested_effect == effect
    assert resolution.clarification_required is clarification
    assert route_tools(resolution) == []


@pytest.mark.parametrize(
    ("message", "intent", "tool_id"),
    [
        ("查看我的体重历史", "weight_history_query", "weight.list_history"),
        ("看看我今天吃了什么", "nutrition_today_query", "nutrition.get_today"),
        ("查看最近饮食记录", "nutrition_history_query", "nutrition.list_history"),
        ("搜索食品鸡胸肉", "food_search_query", "food.search"),
    ],
)
def test_new_private_read_domains_route_to_narrow_read_tools(message, intent, tool_id):
    resolution = resolve_intent(message)

    assert resolution.primary_intent == intent
    assert resolution.request_kind == "query"
    assert route_tools(resolution) == [tool_id]


def test_weight_log_write_is_structured_without_becoming_profile_query():
    resolution = resolve_intent("记录体重65公斤")

    assert resolution.intent_domain == "profile"
    assert resolution.request_kind == "mutation"
    assert resolution.requested_effect == "create"
    assert resolution.change_requests[0].field_path == "weight_log.weight_kg"
    assert resolution.change_requests[0].value == 65.0
    assert route_tools(resolution) == []


def test_three_day_training_advice_is_consultation_not_write():
    resolution = resolve_intent("怎样安排三天训练")

    assert resolution.request_kind == "query"
    assert resolution.requested_effect == "read"
    assert resolution.change_requests == []


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("确认刚才的调整", "confirm"),
        ("拒绝这个方案", "reject"),
    ],
)
def test_natural_language_proposal_decision_is_structured(message, expected_action):
    resolution = resolve_intent(message)

    assert resolution.intent_domain == "workout_plan"
    assert resolution.request_kind == "proposal_decision"
    assert resolution.requested_effect == "decide"
    assert resolution.change_requests[0].field_path == "proposal.status"
    assert resolution.change_requests[0].value == expected_action
    assert route_tools(resolution) == []
