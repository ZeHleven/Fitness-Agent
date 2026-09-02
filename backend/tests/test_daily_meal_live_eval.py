from argparse import Namespace
import json
from pathlib import Path
import uuid

from sqlalchemy import select

from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from scripts import evaluate_daily_meal_generation_real as live_eval


def _complete_food_rows():
    return [(
        str(item["name_zh"]),
        str(item["category"]),
        float(item["calories_per_100g"]),
        float(item["protein_g"]),
        float(item["carbs_g"]),
        float(item["fat_g"]),
    ) for item in live_eval.BASELINE_FOODS]


def test_live_eval_food_catalog_prerequisite_accepts_baseline_catalog():
    diagnostics = live_eval._food_catalog_prerequisite_diagnostics(
        _complete_food_rows()
    )

    assert diagnostics["active_food_count"] == 20
    assert diagnostics["missing_seed_food_count"] == 0
    assert diagnostics["invalid_nutrition_count"] == 0
    assert all(
        count > 0
        for count in diagnostics["required_category_counts"].values()
    )


def test_live_eval_food_catalog_prerequisite_exposes_incomplete_fixture():
    diagnostics = live_eval._food_catalog_prerequisite_diagnostics([
        ("杂粮饭", "碳水", 130.0, 3.0, 28.0, 1.0),
    ])

    assert diagnostics == {
        "active_food_count": 1,
        "required_seed_food_count": 20,
        "missing_seed_food_count": 20,
        "invalid_nutrition_count": 0,
        "required_category_counts": {
            "蛋白质": 0,
            "碳水": 1,
            "蔬菜": 0,
            "油脂": 0,
        },
    }


def test_live_gate_seeds_food_catalog_before_model_evaluation():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "daily-meal-live-eval.yml"
    ).read_text(encoding="utf-8")

    seed_index = workflow.index("python scripts/seed_foods.py")
    evaluation_index = workflow.index(
        "python backend/scripts/evaluate_daily_meal_generation_real.py"
    )
    assert seed_index < evaluation_index


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
    assert report["intent_timeout_runs"] == 0
    assert report["rules_fallback_runs"] == 0
    assert report["semantic_misroute_runs"] == 0
    assert report["optimizer_unavailable_runs"] == 0
    assert "sensitive database and user details" not in report_text


def test_live_eval_reports_food_catalog_prerequisite_failure(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"
    diagnostics = {
        "active_food_count": 1,
        "required_seed_food_count": 20,
        "missing_seed_food_count": 20,
        "invalid_nutrition_count": 0,
        "required_category_counts": {
            "蛋白质": 0,
            "碳水": 1,
            "蔬菜": 0,
            "油脂": 0,
        },
    }

    async def fail_prerequisites():
        raise live_eval.LiveEvaluationPrerequisiteError(diagnostics)

    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(live_eval, "evaluate", fail_prerequisites)
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(strict=True, output=output),
    )

    assert live_eval.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fatal_error"] == {
        "stage": "prerequisites",
        "type": "food_catalog_incomplete",
    }
    assert report["prerequisites"] == diagnostics


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
