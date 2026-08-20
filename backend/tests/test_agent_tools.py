import bcrypt
import pytest

from app.models.profile import UserProfile
from app.models.user import User
from app.services.agent_tools import build_read_tools


def _user(user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )


@pytest.mark.asyncio
async def test_profile_and_health_tools_use_server_injected_user(db_session):
    db_session.add(_user("tool-user-1", "tool-user-1@example.com"))
    await db_session.commit()
    db_session.add(UserProfile(
        user_id="tool-user-1",
        age=30,
        primary_goal="增肌",
        training_location="健身房",
        injuries=["膝关节"],
        chronic_conditions=[],
        onboarding_completed=True,
    ))
    await db_session.commit()

    profile_tool, health_tool = build_read_tools(
        db_session,
        user_id="tool-user-1",
        allowlist=[
            "profile.get_summary",
            "health.get_screening_summary",
        ],
    )

    profile = await profile_tool.ainvoke({})
    health = await health_tool.ainvoke({})

    assert profile["primary_goal"] == "增肌"
    assert "injuries" not in profile
    assert health["injuries"] == ["膝关节"]
    assert "primary_goal" not in health
    assert "user_id" not in str(profile_tool.args_schema.model_json_schema())
    assert "user_id" not in str(health_tool.args_schema.model_json_schema())


def test_tool_builder_fails_closed_for_unknown_or_write_tool(db_session):
    with pytest.raises(ValueError, match="Unknown or non-read"):
        build_read_tools(
            db_session,
            user_id="tool-user-1",
            allowlist=["workout.complete"],
        )


def test_tool_descriptions_are_specific_and_nonempty(db_session):
    tools = build_read_tools(
        db_session,
        user_id="tool-user-1",
        allowlist=[
            "plan.get_active",
            "workout.get_next",
            "workout.list_history",
            "workout.get_progress",
        ],
    )

    assert len({item.name for item in tools}) == len(tools)
    assert all("示例" in item.description for item in tools)
    assert "整个当前计划" in tools[0].description
    assert "下一次计划训练日" in tools[1].description


def test_every_read_tool_schema_excludes_identity_fields(db_session):
    allowlist = [
        "profile.get_summary",
        "health.get_screening_summary",
        "plan.get_active",
        "workout.get_next",
        "workout.get_active_session",
        "workout.list_history",
        "workout.get_progress",
    ]

    tools = build_read_tools(
        db_session,
        user_id="server-owned-user-id",
        allowlist=allowlist,
    )

    assert len(tools) == 7
    assert all(
        "user_id" not in str(item.args_schema.model_json_schema())
        for item in tools
    )
