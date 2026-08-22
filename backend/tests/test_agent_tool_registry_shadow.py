from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheck,
    ToolRegistryShadowCheckType,
    ToolRegistryShadowMismatchCode,
    ToolRegistryShadowReport,
)
from app.services.agent_tool_registry import TOOL_REGISTRY_V2


_FINGERPRINT_A = "a" * 64
_FINGERPRINT_B = "b" * 64
_COMPARATOR_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_tool_registry_shadow_cases.json"
)
_FACT_KEYS_BY_CHECK_TYPE = {
    "route_allowlist": {"tool_ids"},
    "constructed_tools": {"tools", "tool_id", "langchain_name"},
    "argument_schema": {
        "tools",
        "tool_id",
        "schema_ref",
        "additional_properties",
        "fields",
        "name",
        "type",
        "required",
        "default",
        "minimum",
        "maximum",
    },
    "parallel_policy": {
        "tools",
        "tool_id",
        "mode",
        "side_effects",
        "parallel_safe",
        "conditional_pairs",
        "primary_tool_id",
        "fallback_tool_id",
        "speculative_parallel_allowed",
    },
    "conditional_evidence": {
        "events",
        "primary_tool_id",
        "primary_status",
        "fallback_tool_id",
    },
    "observation_semantics": {
        "observations",
        "tool_id",
        "run_status",
        "classification",
    },
}
_FORBIDDEN_FACT_KEYS = {
    "user_id",
    "message",
    "prompt",
    "arguments",
    "raw_result",
    "result",
    "observation_summary",
    "reply",
    "resource_id",
}


def _load_comparator_cases() -> list[dict[str, Any]]:
    payload = json.loads(_COMPARATOR_CASES_PATH.read_text(encoding="utf-8"))
    assert set(payload) == {"fixture_version", "cases"}
    assert payload["fixture_version"] == "1.0.0"
    return payload["cases"]


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_nested_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_nested_keys(item))
        return keys
    return set()


def test_shadow_report_supports_privacy_safe_matches_and_mismatches():
    match = ToolRegistryShadowCheck(
        check_type="route_allowlist",
        status="match",
        legacy_fingerprint=_FINGERPRINT_A,
        registry_fingerprint=_FINGERPRINT_A,
        legacy_tool_ids=("plan.get_active",),
        registry_tool_ids=("plan.get_active",),
        latency_ms=1,
    )
    mismatch = ToolRegistryShadowCheck(
        check_type="parallel_policy",
        status="mismatch",
        mismatch_codes=("parallel_policy_mismatch",),
        legacy_fingerprint=_FINGERPRINT_A,
        registry_fingerprint=_FINGERPRINT_B,
        legacy_tool_ids=("workout.get_progress",),
        registry_tool_ids=("workout.get_progress",),
        latency_ms=1,
    )

    report = ToolRegistryShadowReport(
        registry_version=TOOL_REGISTRY_V2.registry_version,
        status="mismatch",
        sample_bucket=42,
        checks=(match, mismatch),
        total_latency_ms=2,
    )

    assert report.mode == "shadow"
    assert report.status == "mismatch"
    assert report.checks[1].mismatch_codes == (
        "parallel_policy_mismatch",
    )


def test_shadow_contract_rejects_inconsistent_status_payloads():
    with pytest.raises(
        ValidationError,
        match="mismatch checks require a stable mismatch code",
    ):
        ToolRegistryShadowCheck(
            check_type="route_allowlist",
            status="mismatch",
        )

    with pytest.raises(
        ValidationError,
        match="skipped checks require a reason",
    ):
        ToolRegistryShadowCheck(
            check_type="observation_semantics",
            status="skipped",
        )

    with pytest.raises(
        ValidationError,
        match="match reports require only matching checks",
    ):
        ToolRegistryShadowReport(
            registry_version=TOOL_REGISTRY_V2.registry_version,
            status="match",
            sample_bucket=1,
            checks=(),
        )


def test_shadow_report_schema_excludes_user_content_and_tool_payloads():
    schema = json.dumps(
        ToolRegistryShadowReport.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )

    for forbidden_field in (
        "user_id",
        "message",
        "prompt",
        "arguments",
        "raw_result",
        "observation_summary",
    ):
        assert forbidden_field not in schema


def test_shadow_design_does_not_activate_the_registry():
    assert TOOL_REGISTRY_V2.status == "design_only"


def test_comparator_case_fixtures_cover_every_check_and_stable_difference():
    cases = _load_comparator_cases()
    check_types = set(get_args(ToolRegistryShadowCheckType))
    comparable_codes = set(get_args(ToolRegistryShadowMismatchCode)) - {
        "shadow_internal_error"
    }

    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["check_type"] for case in cases} == check_types
    assert {
        code
        for case in cases
        for code in case["expected"]["mismatch_codes"]
    } == comparable_codes

    for check_type in check_types:
        statuses = {
            case["expected"]["status"]
            for case in cases
            if case["check_type"] == check_type
        }
        assert statuses == {"match", "mismatch"}


def test_comparator_case_fixtures_are_well_formed_and_privacy_safe():
    cases = _load_comparator_cases()
    stable_codes = set(get_args(ToolRegistryShadowMismatchCode))

    for case in cases:
        assert set(case) == {
            "case_id",
            "check_type",
            "legacy_fact",
            "registry_fact",
            "expected",
        }
        assert set(case["expected"]) == {"status", "mismatch_codes"}
        assert case["expected"]["status"] in {"match", "mismatch"}
        assert set(case["expected"]["mismatch_codes"]) <= stable_codes
        if case["expected"]["status"] == "match":
            assert case["expected"]["mismatch_codes"] == []
        else:
            assert case["expected"]["mismatch_codes"]

        allowed_keys = _FACT_KEYS_BY_CHECK_TYPE[case["check_type"]]
        fact_keys = _nested_keys(case["legacy_fact"]) | _nested_keys(
            case["registry_fact"]
        )
        assert fact_keys <= allowed_keys
        assert fact_keys.isdisjoint(_FORBIDDEN_FACT_KEYS)


def test_comparator_case_fixtures_have_stable_canonical_json():
    cases = _load_comparator_cases()

    for case in cases:
        for fact_name in ("legacy_fact", "registry_fact"):
            canonical = json.dumps(
                case[fact_name],
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            assert json.loads(canonical) == case[fact_name]
