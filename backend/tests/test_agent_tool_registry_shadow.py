from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheck,
    ToolRegistryShadowReport,
)
from app.services.agent_tool_registry import TOOL_REGISTRY_V2


_FINGERPRINT_A = "a" * 64
_FINGERPRINT_B = "b" * 64


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
