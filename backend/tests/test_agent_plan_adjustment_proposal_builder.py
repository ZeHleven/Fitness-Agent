from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
)
from app.services.agent_plan_adjustment_proposals import (
    PlanAdjustmentProposalCreationRejected,
    PlanAdjustmentProposalPayloadRejected,
    build_validated_plan_adjustment_proposal,
    canonical_plan_adjustment_proposal_payload_data,
    evaluate_plan_adjustment_proposal_creation,
    plan_adjustment_proposal_payload_fingerprint,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_CREATED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _canonical_payloads() -> dict[str, dict]:
    fixture = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return fixture["canonical_payloads"]


def _eligible_decision(*, ttl_hours: int | None = None):
    return evaluate_plan_adjustment_proposal_creation(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        evidence_state="complete",
        draft_state="valid",
        proposal_type="plan_adjustment_v1",
        requested_ttl_hours=ttl_hours,
    )


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"risk_level": "unknown"}, "health_red_flag"),
        ({"evidence_state": "unknown"}, "supporting_evidence_missing"),
        ({"draft_state": "unknown"}, "proposal_draft_invalid"),
        ({"requested_ttl_hours": 0}, "proposal_ttl_out_of_range"),
        ({"requested_ttl_hours": True}, "proposal_ttl_out_of_range"),
    ],
)
def test_creation_gate_fails_closed_for_unknown_or_invalid_facts(
    overrides,
    reason_code,
):
    facts = {
        "feature_enabled": True,
        "run_owned": True,
        "selected_outcome": "adjustment_proposal",
        "terminal_action": "proposal",
        "intent_allows_adjustment": True,
        "risk_level": "low",
        "clarification_required": False,
        "evidence_state": "complete",
        "draft_state": "valid",
        "proposal_type": "plan_adjustment_v1",
        "requested_ttl_hours": None,
    }
    facts.update(overrides)

    decision = evaluate_plan_adjustment_proposal_creation(**facts)

    assert decision.eligible is False
    assert decision.reason_code == reason_code


def _build(payload: dict, *, ttl_hours: int | None = None):
    return build_validated_plan_adjustment_proposal(
        decision=_eligible_decision(ttl_hours=ttl_hours),
        payload_data=payload,
        expected_base_plan_id=payload["target"]["base_plan_id"],
        expected_base_plan_fingerprint=payload["target"][
            "base_plan_fingerprint"
        ],
        created_at=_CREATED_AT,
    )


def test_builder_normalizes_both_canonical_payloads_without_mutating_inputs():
    for original in _canonical_payloads().values():
        payload = copy.deepcopy(original)
        built = _build(payload)

        assert payload == original
        assert built.initial_status == "pending_confirmation"
        assert built.ttl_hours == 24
        assert built.expires_at == _CREATED_AT + timedelta(hours=24)
        assert canonical_plan_adjustment_proposal_payload_data(
            built.payload
        ) == original
        assert built.payload_fingerprint == (
            plan_adjustment_proposal_payload_fingerprint(built.payload)
        )


def test_builder_fingerprint_is_sha256_of_stable_canonical_json():
    original = _canonical_payloads()["adherence"]
    reordered = dict(reversed(list(original.items())))
    first = _build(original, ttl_hours=72)
    second = _build(reordered, ttl_hours=72)
    canonical_json = json.dumps(
        canonical_plan_adjustment_proposal_payload_data(first.payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert first.payload_fingerprint == second.payload_fingerprint
    assert first.payload_fingerprint == hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()
    assert first.expires_at == _CREATED_AT + timedelta(hours=72)


def test_rejected_gate_short_circuits_before_payload_validation():
    decision = evaluate_plan_adjustment_proposal_creation(
        feature_enabled=False,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        evidence_state="complete",
        draft_state="valid",
        proposal_type="plan_adjustment_v1",
        requested_ttl_hours=None,
    )

    with pytest.raises(
        PlanAdjustmentProposalCreationRejected,
        match="feature_disabled",
    ) as raised:
        build_validated_plan_adjustment_proposal(
            decision=decision,
            payload_data={"raw_result": "must-not-be-processed"},
            expected_base_plan_id="ignored",
            expected_base_plan_fingerprint="ignored",
            created_at=_CREATED_AT,
        )

    assert raised.value.reason_code == "feature_disabled"


def test_builder_rejects_private_or_mismatched_payloads_with_stable_codes():
    private_payload = copy.deepcopy(_canonical_payloads()["adherence"])
    private_payload["user_id"] = "private-user-value"

    with pytest.raises(PlanAdjustmentProposalPayloadRejected) as private:
        _build(private_payload)
    assert private.value.error_codes == ("forbidden_field",)
    assert "private-user-value" not in str(private.value)

    payload = _canonical_payloads()["adherence"]
    with pytest.raises(PlanAdjustmentProposalPayloadRejected) as mismatch:
        build_validated_plan_adjustment_proposal(
            decision=_eligible_decision(),
            payload_data=payload,
            expected_base_plan_id="another-active-plan",
            expected_base_plan_fingerprint=payload["target"][
                "base_plan_fingerprint"
            ],
            created_at=_CREATED_AT,
        )
    assert mismatch.value.error_codes == ("invalid_target",)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("missing_plan", "plan_evidence_missing"),
        ("missing_support", "supporting_evidence_missing"),
        ("unknown_tool", "supporting_evidence_missing"),
        ("future_evidence", "supporting_evidence_missing"),
        ("duplicate_evidence", "proposal_draft_invalid"),
    ],
)
def test_builder_rechecks_actual_evidence_after_gate(
    mutation,
    reason_code,
):
    payload = copy.deepcopy(_canonical_payloads()["adherence"])
    if mutation == "missing_plan":
        payload["evidence"] = payload["evidence"][1:]
    elif mutation == "missing_support":
        payload["evidence"] = payload["evidence"][:1]
    elif mutation == "unknown_tool":
        payload["evidence"][1]["tool_id"] = "unknown.get_private"
    elif mutation == "future_evidence":
        payload["evidence"][1]["observed_at"] = (
            _CREATED_AT + timedelta(seconds=1)
        ).isoformat()
    else:
        payload["evidence"].append(copy.deepcopy(payload["evidence"][1]))

    with pytest.raises(
        PlanAdjustmentProposalCreationRejected,
        match=reason_code,
    ) as raised:
        _build(payload)
    assert raised.value.reason_code == reason_code


def test_builder_accepts_an_already_validated_immutable_payload():
    raw = _canonical_payloads()["health"]
    validated = PlanAdjustmentProposalPayload.model_validate(raw)
    built = build_validated_plan_adjustment_proposal(
        decision=_eligible_decision(),
        payload_data=validated,
        expected_base_plan_id=validated.target.base_plan_id,
        expected_base_plan_fingerprint=(
            validated.target.base_plan_fingerprint
        ),
        created_at=_CREATED_AT,
    )

    assert built.payload is validated
