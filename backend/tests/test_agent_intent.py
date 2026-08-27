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
