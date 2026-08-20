from app.services.agent_intent import resolve_intent, route_tools


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
