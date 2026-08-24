"""Run a privacy-safe internal Registry read-enforcement observation window."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Scenario:
    key: str
    message: str
    expected_mode: str
    expected_allowlist: tuple[str, ...]
    expected_actions: tuple[str, ...]
    expected_terminal_actions: tuple[str, ...]
    expected_model_calls: int
    expected_tool_calls: int


SCENARIOS = (
    Scenario(
        key="direct_next_workout",
        message="我下一练做什么？",
        expected_mode="direct",
        expected_allowlist=("workout.get_next",),
        expected_actions=("workout.get_next",),
        expected_terminal_actions=("answer",),
        expected_model_calls=2,
        expected_tool_calls=1,
    ),
    Scenario(
        key="low_adherence_adjustment",
        message="结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。",
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
            "profile.get_summary",
        ),
        expected_actions=(
            "plan.get_active",
            "workout.get_progress",
            "profile.get_summary",
        ),
        expected_terminal_actions=("proposal",),
        expected_model_calls=2,
        expected_tool_calls=3,
    ),
    Scenario(
        key="high_adherence_counterfactual",
        message="结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。",
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
            "profile.get_summary",
        ),
        expected_actions=(
            "plan.get_active",
            "workout.get_progress",
            "profile.get_summary",
        ),
        expected_terminal_actions=("answer",),
        expected_model_calls=2,
        expected_tool_calls=3,
    ),
    Scenario(
        key="progress_fallback_readiness",
        message=(
            "结合最近四周训练情况，判断当前计划是否需要调整；"
            "如果进度统计暂时不可用，请基于最近训练历史给出保守建议。"
        ),
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
        ),
        expected_actions=("plan.get_active", "workout.get_progress"),
        expected_terminal_actions=("answer", "proposal"),
        expected_model_calls=2,
        expected_tool_calls=2,
    ),
    Scenario(
        key="knee_limit_conflict",
        message="我膝盖最近不舒服，结合健康筛查和下一练，告诉我哪些内容需要避开。",
        expected_mode="planned",
        expected_allowlist=(
            "health.get_screening_summary",
            "workout.get_next",
        ),
        expected_actions=(
            "health.get_screening_summary",
            "workout.get_next",
        ),
        expected_terminal_actions=("proposal",),
        expected_model_calls=2,
        expected_tool_calls=2,
    ),
    Scenario(
        key="red_flag_safe_stop",
        message="我现在胸痛，结合下一练告诉我还能不能继续训练。",
        expected_mode="safe_stop",
        expected_allowlist=(),
        expected_actions=(),
        expected_terminal_actions=("safe_stop",),
        expected_model_calls=0,
        expected_tool_calls=0,
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(response: httpx.Response, expected: int) -> Any:
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text[:300]}"
        )
    if not response.content:
        return None
    return response.json()


def _target_reps(value: str | None) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    return max(numbers) if numbers else 12


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _profile_payload(*, injuries: list[str]) -> dict[str, Any]:
    return {
        "age": 30,
        "gender": "prefer_not_to_say",
        "height_cm": 170,
        "weight_kg": 65,
        "experience_level": "beginner",
        "primary_goal": "general_fitness",
        "training_days_per_week": 3,
        "session_duration_min": 45,
        "training_location": "gym",
        "diet_restriction": "none",
        "injuries": injuries,
        "chronic_conditions": [],
        "onboarding_completed": True,
    }


def _create_plan(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    preview = _require(
        client.post(
            f"{api_url}/workouts/plans/personalized/preview",
            headers=headers,
            json={
                "goal": "general_fitness",
                "duration_weeks": 4,
                "days_per_week": 3,
                "session_duration_min": 45,
            },
            timeout=120,
        ),
        200,
    )
    confirmation = {
        key: preview[key]
        for key in (
            "name",
            "goal",
            "duration_weeks",
            "days_per_week",
            "session_duration_min",
            "rationale",
            "safety_notes",
            "exercises",
        )
    }
    return _require(
        client.post(
            f"{api_url}/workouts/plans/personalized/confirm",
            headers=headers,
            json=confirmation,
            timeout=120,
        ),
        201,
    )


def _session_exercises(plan: dict[str, Any]) -> list[dict[str, Any]]:
    first_day = min(item["day_of_week"] for item in plan["exercises"])
    selected = [
        item for item in plan["exercises"] if item["day_of_week"] == first_day
    ]
    return [
        {
            "exercise_id": item["exercise_id"],
            "sets_data": [{"reps": _target_reps(item["reps"]), "weight_kg": 16}],
        }
        for item in selected
    ]


def _seed_session(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    plan_id: str,
    trained_at: date,
    exercises: list[dict[str, Any]],
) -> None:
    _require(
        client.post(
            f"{api_url}/workouts/sessions",
            headers=headers,
            json={
                "trained_at": trained_at.isoformat(),
                "plan_id": plan_id,
                "duration_min": 45,
                "notes": "registry-enforce-observation-synthetic",
                "exercises": exercises,
            },
            timeout=120,
        ),
        201,
    )


def _shadow_summary(trace: dict[str, Any]) -> dict[str, Any] | None:
    shadow = trace.get("tool_registry_shadow")
    if not isinstance(shadow, dict):
        return None
    return {
        "status": shadow.get("status"),
        "total_latency_ms": shadow.get("total_latency_ms"),
        "checks": [
            {
                "check_type": check.get("check_type"),
                "status": check.get("status"),
                "mismatch_codes": check.get("mismatch_codes", []),
                "error_category": check.get("error_category"),
            }
            for check in shadow.get("checks", [])
        ],
    }


def _business_failures(item: dict[str, Any], scenario: Scenario) -> list[str]:
    failures: list[str] = []
    if item.get("status") != "completed":
        failures.append("run_not_completed")
    if item.get("error_code") is not None:
        failures.append("run_error_code_present")
    if item.get("execution_mode") != scenario.expected_mode:
        failures.append("unexpected_execution_mode")
    if tuple(item.get("tool_allowlist", [])) != scenario.expected_allowlist:
        failures.append("unexpected_tool_allowlist")
    if tuple(sorted(item.get("action_tool_ids", []))) != tuple(
        sorted(scenario.expected_actions)
    ):
        failures.append("unexpected_action_tools")
    if item.get("terminal_action") not in scenario.expected_terminal_actions:
        failures.append("unexpected_terminal_action")
    budget = item.get("budget_usage", {})
    if budget.get("model_calls") != scenario.expected_model_calls:
        failures.append("unexpected_model_call_budget")
    if budget.get("tool_calls") != scenario.expected_tool_calls:
        failures.append("unexpected_tool_call_budget")
    if budget.get("replans") != 0:
        failures.append("unexpected_replan")
    if any(status != "completed" for status in item.get("action_statuses", [])):
        failures.append("incomplete_action")
    if any(status != "success" for status in item.get("observation_statuses", [])):
        failures.append("non_success_observation")
    shadow = item.get("shadow")
    if not isinstance(shadow, dict):
        failures.append("missing_shadow_report")
    else:
        checks = shadow.get("checks", [])
        if len(checks) != 6:
            failures.append("unexpected_shadow_check_count")
        if any(check.get("status") in {"mismatch", "error"} for check in checks):
            failures.append("shadow_check_failed")
        if any(check.get("mismatch_codes") for check in checks):
            failures.append("shadow_mismatch_code_present")
        if any(check.get("error_category") for check in checks):
            failures.append("shadow_error_category_present")
    return failures


def _run_scenario(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    scenario: Scenario,
    ordinal: int,
) -> dict[str, Any]:
    started_at = _utc_now()
    timer = time.perf_counter()
    chat = _require(
        client.post(
            f"{api_url}/agent/chat",
            headers=headers,
            json={"message": scenario.message},
            timeout=240,
        ),
        200,
    )
    elapsed_ms = round((time.perf_counter() - timer) * 1000)
    run = _require(
        client.get(
            f"{api_url}/agent/runs/{chat['run_id']}",
            headers=headers,
            timeout=120,
        ),
        200,
    )
    trace = run.get("execution_trace") or {}
    actions = trace.get("actions", [])
    observations = trace.get("observations", [])
    item = {
        "ordinal": ordinal,
        "scenario": scenario.key,
        "run_id": run.get("id"),
        "conversation_id": chat.get("conversation_id"),
        "request_started_at_utc": started_at,
        "request_completed_at_utc": _utc_now(),
        "http_elapsed_ms": elapsed_ms,
        "status": run.get("status"),
        "duration_ms": run.get("duration_ms"),
        "primary_intent": run.get("primary_intent"),
        "execution_mode": run.get("execution_mode"),
        "tool_allowlist": run.get("tool_allowlist", []),
        "trace_version": trace.get("trace_version"),
        "trace_status": trace.get("status"),
        "terminal_action": trace.get("terminal_action"),
        "termination_reason": trace.get("termination_reason"),
        "budget_usage": trace.get("budget_usage", {}),
        "action_tool_ids": [action.get("tool_id") for action in actions],
        "action_statuses": [action.get("status") for action in actions],
        "observation_tool_ids": [
            observation.get("tool_id") for observation in observations
        ],
        "observation_statuses": [
            observation.get("status") for observation in observations
        ],
        "reply_present": bool(run.get("reply")),
        "card_types": [card.get("type") for card in run.get("cards", [])],
        "shadow": _shadow_summary(trace),
        "error_code": run.get("error_code"),
    }
    item["business_failures"] = _business_failures(item, scenario)
    return item


def run_observation(
    *,
    base_url: str,
    deployment_id: str,
    app_version: str,
    runs_per_scenario: int,
    output_path: Path,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    api_url = f"{base_url}/api/v1"
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "base_url": base_url,
        "deployment_id": deployment_id,
        "app_version": app_version,
        "synthetic_account": True,
        "runs_per_scenario": runs_per_scenario,
        "runs": [],
    }
    email = f"registry-enforce-observation-{uuid.uuid4().hex[:12]}@example.com"
    password = f"Observation-{uuid.uuid4().hex}"

    with httpx.Client(timeout=240) as client:
        _require(client.get(f"{base_url}/health", timeout=60), 200)
        auth = _require(
            client.post(
                f"{api_url}/auth/register",
                json={"email": email, "password": password},
                timeout=120,
            ),
            201,
        )
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        _require(
            client.put(
                f"{api_url}/profile",
                headers=headers,
                json=_profile_payload(injuries=[]),
                timeout=120,
            ),
            200,
        )
        plan = _create_plan(client, api_url=api_url, headers=headers)
        exercises = _session_exercises(plan)
        offsets = (27, 25, 23, 20, 18, 16, 13, 11, 9, 6, 4, 2)
        _seed_session(
            client,
            api_url=api_url,
            headers=headers,
            plan_id=plan["id"],
            trained_at=date.today() - timedelta(days=offsets[0]),
            exercises=exercises,
        )

        report["window_start_utc"] = _utc_now()
        ordinal = 0
        for scenario in SCENARIOS:
            if scenario.key == "high_adherence_counterfactual":
                for offset in offsets[1:]:
                    _seed_session(
                        client,
                        api_url=api_url,
                        headers=headers,
                        plan_id=plan["id"],
                        trained_at=date.today() - timedelta(days=offset),
                        exercises=exercises,
                    )
            if scenario.key == "knee_limit_conflict":
                _require(
                    client.put(
                        f"{api_url}/profile",
                        headers=headers,
                        json=_profile_payload(injuries=["膝关节不适"]),
                        timeout=120,
                    ),
                    200,
                )
            for _ in range(runs_per_scenario):
                ordinal += 1
                try:
                    item = _run_scenario(
                        client,
                        api_url=api_url,
                        headers=headers,
                        scenario=scenario,
                        ordinal=ordinal,
                    )
                except Exception as exc:
                    item = {
                        "ordinal": ordinal,
                        "scenario": scenario.key,
                        "run_id": None,
                        "request_completed_at_utc": _utc_now(),
                        "status": "request_failed",
                        "error_category": type(exc).__name__,
                        "business_failures": ["runner_request_failed"],
                    }
                report["runs"].append(item)
                _write_report(output_path, report)

    report["window_end_utc"] = _utc_now()
    report["attempted_run_count"] = len(report["runs"])
    report["completed_run_count"] = sum(
        item.get("status") == "completed" for item in report["runs"]
    )
    report["run_id_count"] = sum(
        isinstance(item.get("run_id"), str) for item in report["runs"]
    )
    business_failures = [
        f"run_{item['ordinal']}:{failure}"
        for item in report["runs"]
        for failure in item.get("business_failures", [])
    ]
    expected_count = len(SCENARIOS) * runs_per_scenario
    if report["attempted_run_count"] != expected_count:
        business_failures.append("unexpected_attempted_run_count")
    if report["completed_run_count"] != expected_count:
        business_failures.append("incomplete_observation_window")
    if report["run_id_count"] != expected_count:
        business_failures.append("missing_run_ids")
    report["business_gate"] = {
        "passed": not business_failures,
        "failures": business_failures,
        "expected_run_count": expected_count,
    }
    _write_report(output_path, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an internal Registry read-enforcement observation window"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.runs_per_scenario < 1:
        print("runs per scenario must be positive")
        return 2
    if args.describe:
        print(json.dumps(
            {
                "scenario_count": len(SCENARIOS),
                "runs_per_scenario": args.runs_per_scenario,
                "expected_run_count": len(SCENARIOS) * args.runs_per_scenario,
                "scenarios": [scenario.key for scenario in SCENARIOS],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    report = run_observation(
        base_url=args.base_url,
        deployment_id=args.deployment_id,
        app_version=args.app_version,
        runs_per_scenario=args.runs_per_scenario,
        output_path=Path(args.output),
    )
    print(json.dumps({
        "output": str(Path(args.output).resolve()),
        "attempted_run_count": report["attempted_run_count"],
        "completed_run_count": report["completed_run_count"],
        "business_gate": report["business_gate"],
        "window_start_utc": report["window_start_utc"],
        "window_end_utc": report["window_end_utc"],
    }, ensure_ascii=False, indent=2))
    return 1 if args.strict and not report["business_gate"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
