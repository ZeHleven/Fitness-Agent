from argparse import Namespace
import json
import uuid

from sqlalchemy import select

from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from scripts import evaluate_daily_meal_generation_real as live_eval


async def test_live_eval_seed_persists_user_before_dependent_records(db_session):
    suffix = f"test-{uuid.uuid4().hex[:12]}"

    user_id = await live_eval._seed_evaluation_subject(
        db_session,
        suffix=suffix,
    )

    assert await db_session.get(User, user_id) is not None
    assert await db_session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ) is not None
    assert await db_session.scalar(
        select(WeightLog).where(WeightLog.user_id == user_id)
    ) is not None


def test_live_eval_writes_sanitized_report_for_fatal_evaluation_error(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"

    async def fail_evaluation():
        raise RuntimeError("sensitive database and user details")

    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(live_eval, "evaluate", fail_evaluation)
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(strict=True, output=output),
    )

    assert live_eval.main() == 1
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["passed"] is False
    assert report["fatal_error"] == {
        "stage": "evaluation",
        "type": "RuntimeError",
    }
    assert "sensitive database and user details" not in report_text


def test_live_eval_missing_key_still_writes_diagnostic_artifact(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"
    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(strict=True, output=output),
    )

    assert live_eval.main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fatal_error"] == {
        "stage": "configuration",
        "type": "missing_deepseek_api_key",
    }
