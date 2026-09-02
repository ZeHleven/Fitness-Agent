from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.agent import (  # noqa: E402
    AgentArtifact,
    AgentConversation,
    AgentProposal,
    AgentRun,
)
from app.models.meal import MealLog  # noqa: E402
from app.models.profile import UserProfile, WeightLog  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.agent_runtime import run_agent_chat  # noqa: E402


ORIGINAL = "结合我的情况安排今天怎么吃"
SYNONYMS = (
    "参考我的身体情况，给我安排今天三餐",
    "按我今天的训练量规划一整天怎么吃",
    "结合我的档案和最近状态制定今日饮食",
    "看看我近期训练和体重，安排今天的饭",
    "根据我的健身目标给出今天全天食谱",
    "今天训练的话，我三餐应该怎么搭配",
    "按我的增肌目标规划今天的早餐午餐晚餐",
    "读取必要资料后帮我制定今天饮食方案",
    "根据我最近的饮食和训练安排今日三餐",
    "给我做一份符合当前状态的全天饮食",
    "结合体重趋势帮我安排今天吃什么",
    "参考我的健康情况生成今日三餐方案",
    "今天该怎么吃，请按我的实际情况安排",
    "按照我的训练频率设计今天的饮食",
    "为我规划今天一整天的健身饮食",
    "综合我的资料给我配今天三顿饭",
    "根据近期执行情况安排今天的营养摄入",
    "今天是训练日的话帮我搭配全天餐食",
    "按当前目标和身体数据给我制定今日食谱",
    "请自动读取需要的信息并安排今天怎么吃",
)


async def _seed_evaluation_subject(db: AsyncSession, *, suffix: str) -> str:
    """Create the isolated live-evaluation subject in dependency order."""
    user_id = f"daily-meal-live-{suffix}"
    user = User(
        id=user_id,
        email=f"daily-meal-live-{suffix}@example.invalid",
        password_hash="live-eval-not-used",
    )
    db.add(user)
    # These models intentionally do not expose ORM relationships. Flush the
    # foreign-key parent first so SQLAlchemy cannot order the profile/weight
    # inserts ahead of the user on PostgreSQL.
    await db.flush()

    db.add_all([
        UserProfile(
            user_id=user_id,
            age=30,
            gender="prefer_not_to_say",
            height_cm=170,
            weight_kg=66,
            primary_goal="增肌",
            training_days_per_week=3,
            diet_restriction=None,
            injuries=[],
            chronic_conditions=[],
            onboarding_completed=True,
        ),
        WeightLog(user_id=user_id, weight_kg=66),
    ])
    await db.commit()
    return user_id


async def evaluate() -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    prompts = [ORIGINAL] * 10 + list(SYNONYMS)
    results: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        user_id = await _seed_evaluation_subject(db, suffix=suffix)

        for index, prompt in enumerate(prompts, start=1):
            conversation = AgentConversation(
                user_id=user_id,
                title=f"live-eval-{index}",
            )
            db.add(conversation)
            await db.commit()
            failure: str | None = None
            result = None
            try:
                result = await run_agent_chat(
                    db,
                    user_id=user_id,
                    conversation=conversation,
                    user_message=prompt,
                )
            except Exception as exc:  # diagnostic boundary for the eval report
                failure = type(exc).__name__
                await db.rollback()

            run = None
            artifact = None
            if result is not None:
                run = await db.get(AgentRun, result.run_id)
                artifact = await db.scalar(select(AgentArtifact).where(
                    AgentArtifact.source_run_id == result.run_id
                ))
            fit_status = None
            if artifact is not None:
                fit_status = artifact.payload_data.get("nutrition_fit", {}).get(
                    "status"
                )
            success = bool(
                run is not None
                and run.request_kind == "generation"
                and run.requested_effect == "read"
                and run.requested_output == "daily_meal_plan"
                and artifact is not None
                and fit_status in {"within_target", "acceptable_deviation"}
            )
            results.append({
                "case": index,
                "group": "original" if index <= 10 else "synonym",
                "success": success,
                "request_kind": run.request_kind if run is not None else None,
                "termination_reason": (
                    (run.execution_trace or {}).get("termination_reason")
                    if run is not None
                    else failure
                ),
                "fit_status": fit_status,
            })

        meal_count = await db.scalar(select(func.count(MealLog.id)).where(
            MealLog.user_id == user_id
        ))
        proposal_count = await db.scalar(select(func.count(AgentProposal.id)).where(
            AgentProposal.user_id == user_id
        ))

    original = [item for item in results if item["group"] == "original"]
    synonyms = [item for item in results if item["group"] == "synonym"]
    original_successes = sum(bool(item["success"]) for item in original)
    synonym_successes = sum(bool(item["success"]) for item in synonyms)
    mutation_misroutes = sum(
        item["request_kind"] == "mutation" for item in results
    )
    fit_counts = {
        status: sum(item["fit_status"] == status for item in results)
        for status in ("within_target", "acceptable_deviation")
    }
    return {
        "model": settings.AGENT_MODEL,
        "original_runs": len(original),
        "original_successes": original_successes,
        "synonym_runs": len(synonyms),
        "synonym_successes": synonym_successes,
        "synonym_success_rate": synonym_successes / len(synonyms),
        "generation_as_mutation_count": mutation_misroutes,
        "unconfirmed_meal_writes": int(meal_count or 0),
        "proposal_count": int(proposal_count or 0),
        "fit_counts": fit_counts,
        "passed": (
            original_successes == 10
            and synonym_successes / len(synonyms) >= 0.95
            and mutation_misroutes == 0
            and int(meal_count or 0) == 0
            and int(proposal_count or 0) == 0
        ),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the protected real-model daily meal release gate"
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _fatal_report(*, stage: str, error_type: str) -> dict[str, Any]:
    """Return a stable report without exception messages or sensitive values."""
    return {
        "model": settings.AGENT_MODEL,
        "original_runs": 0,
        "original_successes": 0,
        "synonym_runs": 0,
        "synonym_successes": 0,
        "synonym_success_rate": 0.0,
        "generation_as_mutation_count": 0,
        "unconfirmed_meal_writes": 0,
        "proposal_count": 0,
        "fit_counts": {
            "within_target": 0,
            "acceptable_deviation": 0,
        },
        "passed": False,
        "fatal_error": {
            "stage": stage,
            "type": error_type,
        },
        "results": [],
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if output:
        output.write_text(encoded + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not settings.DEEPSEEK_API_KEY:
        _write_report(
            _fatal_report(
                stage="configuration",
                error_type="missing_deepseek_api_key",
            ),
            args.output,
        )
        print("DEEPSEEK_API_KEY is required", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(evaluate())
    except Exception as exc:  # keep the Actions artifact safe and actionable
        report = _fatal_report(
            stage="evaluation",
            error_type=type(exc).__name__,
        )
        _write_report(report, args.output)
        return 1
    _write_report(report, args.output)
    return 1 if args.strict and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
