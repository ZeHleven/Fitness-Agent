"""Pure construction boundary for validated plan-adjustment proposals."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationDecision,
    PlanAdjustmentProposalCreationReasonCode,
    PlanAdjustmentProposalDraft,
    PlanAdjustmentProposalEvidence,
    PlanAdjustmentPlanSnapshot,
    PlanAdjustmentProposalPayload,
    PlanAdjustmentProposalPayloadErrorCode,
    PlanAdjustmentExerciseReplacementChange,
    PlanAdjustmentExerciseTargetChange,
    PlanAdjustmentScheduleChange,
    ValidatedPlanAdjustmentProposal,
    plan_adjustment_proposal_payload_error_codes,
)
from app.services.agent_intent import ExplicitPlanAdjustmentCommand
from app.services.agent_trace import observation_fingerprint


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


@dataclass(frozen=True)
class RuntimePlanAdjustmentProposalBuildResult:
    decision: PlanAdjustmentProposalCreationDecision
    built: ValidatedPlanAdjustmentProposal | None = None


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
    supporting_evidence_required: bool = True,
) -> None:
    evidence_tool_ids = {item.tool_id for item in payload.evidence}
    if "plan.get_active" not in evidence_tool_ids:
        raise PlanAdjustmentProposalCreationRejected(
            "plan_evidence_missing"
        )
    if supporting_evidence_required and not (
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
    supporting_evidence_required: bool = True,
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
    _validate_evidence(
        payload,
        created_at=created_at,
        supporting_evidence_required=supporting_evidence_required,
    )

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


def _active_plan_observation(
    observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        result = observation.get("result")
        if (
            observation.get("tool_id") == "plan.get_active"
            and observation.get("status") == "success"
            and isinstance(result, dict)
            and result.get("found") is True
            and isinstance(result.get("plan"), dict)
        ):
            return observation
    return None


def _runtime_evidence_state(
    observations: list[dict[str, Any]],
    *,
    supporting_evidence_required: bool = True,
) -> str:
    if _active_plan_observation(observations) is None:
        return "plan_missing"
    if supporting_evidence_required and not any(
        observation.get("tool_id")
        in PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
        and observation.get("status") == "success"
        and isinstance(observation.get("result"), dict)
        for observation in observations
    ):
        return "supporting_missing"
    return "complete"


def _explicit_duration_proposal_draft(
    command: ExplicitPlanAdjustmentCommand,
) -> PlanAdjustmentProposalDraft:
    before = command.expected_duration_weeks
    after = command.target_duration_weeks
    return PlanAdjustmentProposalDraft.model_validate({
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"duration_weeks": before},
            "after": {"duration_weeks": after},
            "reason": (
                f"按用户明确指令将计划周期从{before}周调整为{after}周，"
                "其他内容保持不变。"
            ),
            "safety_priority": False,
        }],
        "rationale": [
            f"用户明确要求将当前计划周期从{before}周调整为{after}周。"
        ],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    })


def _plan_snapshot_from_observation(
    observation: dict[str, Any],
) -> tuple[str, PlanAdjustmentPlanSnapshot]:
    result = observation["result"]
    plan = result["plan"]
    plan_id = plan.get("id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("active plan observation has no stable plan id")
    exercises = plan.get("exercises")
    if not isinstance(exercises, list) or not exercises:
        raise ValueError("active plan observation has no exercises")

    normalized_exercises: list[dict[str, Any]] = []
    for item in exercises:
        if not isinstance(item, dict):
            raise ValueError("active plan exercise is not an object")
        day_of_week = item.get("day_of_week")
        order_index = item.get("order_index")
        exercise_name = item.get("exercise_name")
        if (
            not isinstance(day_of_week, int)
            or isinstance(day_of_week, bool)
            or not isinstance(order_index, int)
            or isinstance(order_index, bool)
            or not isinstance(exercise_name, str)
            or not exercise_name
        ):
            raise ValueError("active plan exercise identity is incomplete")
        normalized_exercises.append({
            "slot_key": f"day-{day_of_week}-order-{order_index}",
            "exercise_id": item.get("exercise_id"),
            "exercise_name": exercise_name,
            "day_of_week": day_of_week,
            "sets": item.get("sets"),
            "reps": item.get("reps"),
            "rest_seconds": item.get("rest_seconds"),
            "recommended_weight_kg": item.get("recommended_weight_kg"),
            "order_index": order_index,
        })

    snapshot = PlanAdjustmentPlanSnapshot.model_validate({
        "name": plan.get("name"),
        "goal": plan.get("goal"),
        "duration_weeks": plan.get("duration_weeks"),
        "days_per_week": plan.get("days_per_week"),
        "exercises": normalized_exercises,
    })
    return plan_id, snapshot


def plan_adjustment_plan_snapshot_fingerprint(
    snapshot: PlanAdjustmentPlanSnapshot,
) -> str:
    canonical = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_plan_adjustment_proposal_draft(
    before: PlanAdjustmentPlanSnapshot,
    draft: PlanAdjustmentProposalDraft,
) -> PlanAdjustmentPlanSnapshot:
    after_data = deepcopy(before.model_dump(mode="python"))
    exercises_by_slot = {
        item["slot_key"]: item for item in after_data["exercises"]
    }
    seen_changes: set[tuple[str, str]] = set()

    for change in draft.changes:
        change_key = (change.change_type, change.stable_display_key)
        if change_key in seen_changes:
            raise ValueError("proposal draft repeats a change target")
        seen_changes.add(change_key)

        if isinstance(change, PlanAdjustmentExerciseReplacementChange):
            raise ValueError("exercise replacement is outside the first cohort")
        if isinstance(change, PlanAdjustmentScheduleChange):
            changed_fields = change.before.model_fields_set
            if "days_per_week" in changed_fields:
                raise ValueError("schedule frequency changes require a later cohort")
            for field_name in changed_fields:
                if after_data[field_name] != getattr(change.before, field_name):
                    raise ValueError("schedule draft does not match active plan")
                after_data[field_name] = getattr(change.after, field_name)
            continue
        if not isinstance(change, PlanAdjustmentExerciseTargetChange):
            raise ValueError("proposal draft change type is unsupported")

        exercise = exercises_by_slot.get(change.stable_display_key)
        if exercise is None:
            raise ValueError("proposal draft target is not in active plan")
        for field_name in change.before.model_fields_set:
            if exercise[field_name] != getattr(change.before, field_name):
                raise ValueError("proposal draft before value is stale")
            exercise[field_name] = getattr(change.after, field_name)

    after = PlanAdjustmentPlanSnapshot.model_validate(after_data)
    if after == before:
        raise ValueError("proposal draft has no effect")
    return after


def _runtime_evidence(
    observations: list[dict[str, Any]],
    *,
    observed_at: datetime,
) -> tuple[PlanAdjustmentProposalEvidence, ...]:
    evidence: list[PlanAdjustmentProposalEvidence] = []
    seen: set[tuple[str, str]] = set()
    for observation in observations:
        tool_id = observation.get("tool_id")
        result = observation.get("result")
        if (
            observation.get("status") != "success"
            or not isinstance(tool_id, str)
            or tool_id
            not in (
                PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
                | {"plan.get_active"}
            )
            or not isinstance(result, dict)
        ):
            continue
        result_fingerprint = observation_fingerprint(result)
        evidence_key = (tool_id, result_fingerprint)
        if evidence_key in seen:
            continue
        seen.add(evidence_key)
        evidence.append(PlanAdjustmentProposalEvidence(
            tool_id=tool_id,
            result_fingerprint=result_fingerprint,
            observed_at=observed_at,
        ))
    return tuple(evidence)


def _profile_training_days_per_week(
    observations: list[dict[str, Any]],
) -> int | None:
    for observation in reversed(observations):
        if (
            observation.get("tool_id") != "profile.get_summary"
            or observation.get("status") != "success"
        ):
            continue
        result = observation.get("result")
        if not isinstance(result, dict) or result.get("found") is not True:
            continue
        value = result.get("training_days_per_week")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= 7
        ):
            return value
    return None


def _is_unsupported_frequency_workaround(
    *,
    before: PlanAdjustmentPlanSnapshot,
    draft: PlanAdjustmentProposalDraft,
    observations: list[dict[str, Any]],
) -> bool:
    profile_days = _profile_training_days_per_week(observations)
    if profile_days is None or profile_days >= before.days_per_week:
        return False
    return bool(draft.changes) and all(
        isinstance(change, PlanAdjustmentScheduleChange)
        and change.before.model_fields_set == {"duration_weeks"}
        for change in draft.changes
    )


def build_runtime_plan_adjustment_proposal(
    *,
    feature_enabled: bool,
    run_owned: bool,
    selected_outcome: str,
    terminal_action: str,
    intent_allows_adjustment: bool,
    risk_level: str,
    clarification_required: bool,
    observations: list[dict[str, Any]],
    proposal_draft: Mapping[str, Any] | None,
    explicit_adjustment_command: ExplicitPlanAdjustmentCommand | None = None,
    created_at: datetime,
) -> RuntimePlanAdjustmentProposalBuildResult:
    """Build a server-owned full payload from one compact Finalizer draft."""

    parsed_draft: PlanAdjustmentProposalDraft | None = None
    expects_draft = (
        feature_enabled
        and selected_outcome == "adjustment_proposal"
        and terminal_action == "proposal"
    )
    draft_rejection_reason: PlanAdjustmentProposalCreationReasonCode | None = None
    if feature_enabled and explicit_adjustment_command is not None:
        parsed_draft = _explicit_duration_proposal_draft(
            explicit_adjustment_command
        )
    elif feature_enabled and isinstance(proposal_draft, Mapping):
        try:
            parsed_draft = PlanAdjustmentProposalDraft.model_validate(
                dict(proposal_draft)
            )
        except ValidationError:
            parsed_draft = None
            if expects_draft:
                draft_rejection_reason = "proposal_draft_schema_invalid"
    elif expects_draft:
        draft_rejection_reason = "proposal_draft_missing"

    proposal_type = (
        parsed_draft.proposal_type if parsed_draft is not None else ""
    )
    requested_ttl_hours = (
        parsed_draft.requested_ttl_hours
        if parsed_draft is not None
        else None
    )
    decision = evaluate_plan_adjustment_proposal_creation(
        feature_enabled=feature_enabled,
        run_owned=run_owned,
        selected_outcome=selected_outcome,
        terminal_action=terminal_action,
        intent_allows_adjustment=intent_allows_adjustment,
        risk_level=risk_level,
        clarification_required=clarification_required,
        evidence_state=_runtime_evidence_state(
            observations,
            supporting_evidence_required=(
                explicit_adjustment_command is None
            ),
        ),
        draft_state="valid" if parsed_draft is not None else "invalid",
        proposal_type=proposal_type,
        requested_ttl_hours=requested_ttl_hours,
    )
    if not decision.eligible:
        if (
            decision.reason_code == "proposal_draft_invalid"
            and draft_rejection_reason is not None
        ):
            decision = _rejected(draft_rejection_reason)
        return RuntimePlanAdjustmentProposalBuildResult(decision=decision)
    if parsed_draft is None:  # pragma: no cover - gate/draft parity
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_draft_invalid")
        )

    plan_observation = _active_plan_observation(observations)
    if plan_observation is None:  # pragma: no cover - decision parity
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("plan_evidence_missing")
        )
    try:
        plan_id, before = _plan_snapshot_from_observation(plan_observation)
    except (KeyError, ValidationError, TypeError, ValueError):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_candidate_build_invalid")
        )
    if (
        explicit_adjustment_command is None
        and _is_unsupported_frequency_workaround(
            before=before,
            draft=parsed_draft,
            observations=observations,
        )
    ):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected(
                "proposal_frequency_restructure_unsupported"
            )
        )
    try:
        after = apply_plan_adjustment_proposal_draft(before, parsed_draft)
    except (ValidationError, TypeError, ValueError):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_target_mismatch")
        )
    try:
        base_fingerprint = plan_adjustment_plan_snapshot_fingerprint(before)
        evidence = _runtime_evidence(
            observations,
            observed_at=created_at,
        )
        payload = PlanAdjustmentProposalPayload(
            schema_version="1.0.0",
            proposal_type=parsed_draft.proposal_type,
            target={
                "resource_type": "workout_plan",
                "base_plan_id": plan_id,
                "base_plan_fingerprint": base_fingerprint,
            },
            before=before,
            after=after,
            changes=parsed_draft.changes,
            evidence=evidence,
            rationale=parsed_draft.rationale,
            safety_notes=parsed_draft.safety_notes,
        )
        built = build_validated_plan_adjustment_proposal(
            decision=decision,
            payload_data=payload,
            expected_base_plan_id=plan_id,
            expected_base_plan_fingerprint=base_fingerprint,
            created_at=created_at,
            supporting_evidence_required=(
                explicit_adjustment_command is None
            ),
        )
    except (
        PlanAdjustmentProposalCreationRejected,
        PlanAdjustmentProposalPayloadRejected,
        ValidationError,
        TypeError,
        ValueError,
    ):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_candidate_build_invalid")
        )
    return RuntimePlanAdjustmentProposalBuildResult(
        decision=decision,
        built=built,
    )
