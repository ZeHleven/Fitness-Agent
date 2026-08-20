from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.agent_intent import route_tools  # noqa: E402
from app.services.agent_intent_model import resolve_intent_with_fallback  # noqa: E402


DEFAULT_CASE_FILE = Path(__file__).resolve().parents[1] / "evals" / "agent_intent_cases.json"


async def evaluate(case_file: Path, *, use_model: bool) -> dict[str, Any]:
    all_cases = json.loads(case_file.read_text(encoding="utf-8"))
    cases = [
        case for case in all_cases
        if use_model or not case.get("model_only", False)
    ]
    results: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    primary_correct = 0
    risk_correct = 0
    expected_allowed = 0
    allowed_matched = 0
    forbidden_total = 0
    forbidden_leaks = 0
    clarification_correct = 0

    for case in cases:
        outcome = await resolve_intent_with_fallback(
            case["message"],
            context_messages=case.get("context_messages"),
            pending_clarification=case.get("pending_clarification"),
            use_model=use_model,
        )
        resolution = outcome.resolution
        routed = route_tools(resolution)
        routed_set = set(routed)
        allowed = set(case["allowed_tools"])
        forbidden = set(case["forbidden_tools"])
        primary_ok = resolution.primary_intent == case["expected_primary"]
        risk_ok = resolution.risk_level == case["risk_level"]
        allowed_hits = len(allowed & routed_set)
        leaks = sorted(forbidden & routed_set)
        clarification_ok = (
            resolution.clarification_required
            == case.get("clarification_required", False)
        )

        primary_correct += int(primary_ok)
        risk_correct += int(risk_ok)
        expected_allowed += len(allowed)
        allowed_matched += allowed_hits
        forbidden_total += len(forbidden)
        forbidden_leaks += len(leaks)
        clarification_correct += int(clarification_ok)
        source_counts[outcome.source] += 1
        results.append({
            "id": case["id"],
            "primary": resolution.primary_intent,
            "expected_primary": case["expected_primary"],
            "primary_ok": primary_ok,
            "risk_level": resolution.risk_level,
            "risk_ok": risk_ok,
            "resolved_query": resolution.resolved_query,
            "references": [item.model_dump() for item in resolution.references],
            "subtasks": resolution.subtasks,
            "clarification_required": resolution.clarification_required,
            "clarification_ok": clarification_ok,
            "tools": routed,
            "missing_allowed_tools": sorted(allowed - routed_set),
            "forbidden_tool_leaks": leaks,
            "source": outcome.source,
            "fallback_reason": outcome.fallback_reason,
        })

    total = len(cases)
    return {
        "mode": "model" if use_model else "rules",
        "case_count": total,
        "primary_accuracy": primary_correct / total if total else 0,
        "risk_accuracy": risk_correct / total if total else 0,
        "clarification_accuracy": (
            clarification_correct / total if total else 0
        ),
        "allowed_tool_recall": (
            allowed_matched / expected_allowed if expected_allowed else 1
        ),
        "forbidden_tool_leakage": (
            forbidden_leaks / forbidden_total if forbidden_total else 0
        ),
        "source_counts": dict(source_counts),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Agent intent and tool routing")
    parser.add_argument("--mode", choices=("rules", "model"), default="rules")
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "model" and not settings.DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY is required for --mode model", file=sys.stderr)
        return 2
    report = asyncio.run(evaluate(args.case_file, use_model=args.mode == "model"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and (
        report["primary_accuracy"] < 0.85
        or report["allowed_tool_recall"] < 0.9
        or report["forbidden_tool_leakage"] > 0
        or report["risk_accuracy"] < 1
        or report["clarification_accuracy"] < 1
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
