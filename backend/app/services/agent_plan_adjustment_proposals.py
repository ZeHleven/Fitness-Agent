"""Pure construction boundary for validated plan-adjustment proposals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationDecision,
    PlanAdjustmentProposalCreationReasonCode,
    PlanAdjustmentProposalPayload,
    PlanAdjustmentProposalPayloadErrorCode,
    ValidatedPlanAdjustmentProposal,
    plan_adjustment_proposal_payload_error_codes,
)


PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS = frozenset({
    "profile.get_summary",
    "health.get_screening_summary",
    "workout.list_history",
    "workout.get_progress",
})


class PlanAdjustmentProposalCreationRejected(ValueError):
    def __init__(
        self,
        reason_code: PlanAdjustmentProposalCreationReasonCode,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PlanAdjustmentProposalPayloadRejected(ValueError):
    def __init__(
        self,
        error_codes: tuple[PlanAdjustmentProposalPayloadErrorCode, ...],
    ) -> None:
        self.error_codes = error_codes
        super().__init__(",".join(error_codes))


def _rejected(
    reason_code: PlanAdjustmentProposalCreationReasonCode,
) -> PlanAdjustmentProposalCreationDecision:
    return PlanAdjustmentProposalCreationDecision(
        eligible=False,
        reason_code=reason_code,
    )


def evaluate_plan_adjustment_proposal_creation(
    *,
    feature_enabled: bool,
    run_owned: bool,
    selected_outcome: str,
    terminal_action: str,
    intent_allows_adjustment: bool,
    risk_level: str,
    clarification_required: bool,
    evidence_state: str,
    draft_state: str,
    proposal_type: str,
    requested_ttl_hours: int | None,
) -> PlanAdjustmentProposalCreationDecision:
    """Apply the fixed fail-closed creation gate without runtime settings."""

    if not feature_enabled:
        return _rejected("feature_disabled")
    if not run_owned:
        return _rejected("run_ownership_lost")
    if risk_level not in {"low", "medium", "high"} or risk_level == "high":
        return _rejected("health_red_flag")
    if clarification_required:
        return _rejected("clarification_required")
    if selected_outcome != "adjustment_proposal":
        return _rejected("outcome_not_adjustment_proposal")
    if terminal_action != "proposal":
        return _rejected("terminal_action_not_proposal")
    if not intent_allows_adjustment:
        return _rejected("intent_not_adjustment")
    if evidence_state != "complete":
        reason_by_state: dict[
            str,
            PlanAdjustmentProposalCreationReasonCode,
        ] = {
            "plan_missing": "plan_evidence_missing",
            "supporting_missing": "supporting_evidence_missing",
            "deadline_insufficient": "deadline_evidence_insufficient",
        }
        return _rejected(
            reason_by_state.get(
                evidence_state,
                "supporting_evidence_missing",
            )
        )
    if draft_state != "valid":
        return _rejected(
            "proposal_target_ambiguous"
            if draft_state == "ambiguous_target"
            else "proposal_draft_invalid"
        )
    if proposal_type != "plan_adjustment_v1":
        return _rejected("proposal_type_not_allowed")

    ttl_hours = 24 if requested_ttl_hours is None else requested_ttl_hours
    if (
        isinstance(ttl_hours, bool)
        or not isinstance(ttl_hours, int)
        or not 1 <= ttl_hours <= 72
    ):
        return _rejected("proposal_ttl_out_of_range")
    return PlanAdjustmentProposalCreationDecision(
        eligible=True,
        initial_status="pending_confirmation",
        ttl_hours=ttl_hours,
    )


def canonical_plan_adjustment_proposal_payload_data(
    payload: PlanAdjustmentProposalPayload,
) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude_unset=True)


def plan_adjustment_proposal_payload_fingerprint(
    payload: PlanAdjustmentProposalPayload,
) -> str:
    canonical = json.dumps(
        canonical_plan_adjustment_proposal_payload_data(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_evidence(
    payload: PlanAdjustmentProposalPayload,
    *,
    created_at: datetime,
) -> None:
    evidence_tool_ids = {item.tool_id for item in payload.evidence}
    if "plan.get_active" not in evidence_tool_ids:
        raise PlanAdjustmentProposalCreationRejected(
            "plan_evidence_missing"
        )
    if not (
        evidence_tool_ids & PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
    ):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )
    if not evidence_tool_ids <= (
        PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS | {"plan.get_active"}
    ):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )
    evidence_keys = [
        (item.tool_id, item.result_fingerprint) for item in payload.evidence
    ]
    if len(evidence_keys) != len(set(evidence_keys)):
        raise PlanAdjustmentProposalCreationRejected(
            "proposal_draft_invalid"
        )
    if any(item.observed_at > created_at for item in payload.evidence):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )


def build_validated_plan_adjustment_proposal(
    *,
    decision: PlanAdjustmentProposalCreationDecision,
    payload_data: Mapping[str, Any] | PlanAdjustmentProposalPayload,
    expected_base_plan_id: str,
    expected_base_plan_fingerprint: str,
    created_at: datetime,
) -> ValidatedPlanAdjustmentProposal:
    """Build an immutable proposal result without persistence or side effects."""

    if not decision.eligible:
        if decision.reason_code is None:  # pragma: no cover - schema invariant
            raise ValueError("rejected proposal decision has no reason code")
        raise PlanAdjustmentProposalCreationRejected(decision.reason_code)
    if created_at.tzinfo is None:
        raise ValueError("proposal created_at must include a timezone")

    if isinstance(payload_data, PlanAdjustmentProposalPayload):
        payload = payload_data
    else:
        candidate = dict(payload_data)
        error_codes = plan_adjustment_proposal_payload_error_codes(candidate)
        if error_codes:
            raise PlanAdjustmentProposalPayloadRejected(error_codes)
        try:
            payload = PlanAdjustmentProposalPayload.model_validate(candidate)
        except ValidationError as exc:  # pragma: no cover - classifier parity
            raise PlanAdjustmentProposalPayloadRejected(
                ("forbidden_field",)
            ) from exc

    if (
        payload.target.base_plan_id != expected_base_plan_id
        or payload.target.base_plan_fingerprint
        != expected_base_plan_fingerprint
    ):
        raise PlanAdjustmentProposalPayloadRejected(("invalid_target",))
    _validate_evidence(payload, created_at=created_at)

    if (  # pragma: no cover - schema invariant
        decision.ttl_hours is None or decision.initial_status is None
    ):
        raise ValueError("eligible proposal decision is incomplete")
    return ValidatedPlanAdjustmentProposal(
        initial_status=decision.initial_status,
        ttl_hours=decision.ttl_hours,
        created_at=created_at,
        expires_at=created_at + timedelta(hours=decision.ttl_hours),
        payload=payload,
        payload_fingerprint=plan_adjustment_proposal_payload_fingerprint(
            payload
        ),
    )
