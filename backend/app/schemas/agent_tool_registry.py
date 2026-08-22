from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolIntentName = Literal[
    "profile_query",
    "health_query",
    "plan_query",
    "next_workout_query",
    "active_workout_query",
    "workout_history_query",
    "workout_progress_query",
]


class ToolArgumentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_ref: str = Field(min_length=1, max_length=120)
    default_arguments: dict[str, Any] = Field(default_factory=dict)
    additional_properties: bool = False
    identity_source: Literal["server_context"] = "server_context"


class ToolObservationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_shape: Literal["legacy_mapping", "normalized_envelope"]
    output_schema_ref: str | None = Field(default=None, max_length=120)
    strict_output_schema: bool = False
    missing_data_signals: tuple[
        Literal[
            "found_false",
            "reason_code",
            "count_zero",
            "empty_aggregate",
        ],
        ...,
    ] = ()
    found_false_is_success: bool = False
    empty_collection_is_success: bool = False
    tool_errors_raise: bool = True


class ToolFreshnessContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reuse_scope: Literal["none", "run", "conversation", "user"]
    max_age_seconds: int = Field(ge=0, le=86400)
    invalidation_events: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_reuse_window(self) -> Self:
        if self.reuse_scope == "none" and self.max_age_seconds != 0:
            raise ValueError("non-reusable tools must have a zero freshness window")
        if self.reuse_scope != "none" and self.max_age_seconds == 0:
            raise ValueError("reusable tools must have a positive freshness window")
        return self


class ToolAuditContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argument_storage: Literal["normalized"] = "normalized"
    result_storage: Literal["summary_and_fingerprint"] = (
        "summary_and_fingerprint"
    )
    identity_logged: bool = False
    sensitive_result_fields: tuple[str, ...] = Field(
        default=(),
        max_length=20,
    )


class ToolRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
        max_length=100,
    )
    contract_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        max_length=20,
    )
    langchain_name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=100,
    )
    title: str = Field(min_length=1, max_length=80)
    mode: Literal["read", "proposal", "execute"]
    availability: Literal["active", "hidden", "planned"]
    side_effects: Literal["none", "proposal_only", "writes_data"]
    risk_level: Literal["low", "medium", "high"]
    data_sensitivity: Literal["standard", "personal", "health_sensitive"]
    supported_intents: tuple[ToolIntentName, ...] = Field(
        min_length=1,
        max_length=8,
    )
    use_cases: tuple[str, ...] = Field(min_length=1, max_length=8)
    exclusions: tuple[str, ...] = Field(default=(), max_length=8)
    data_sources: tuple[str, ...] = Field(min_length=1, max_length=8)
    parallel_safe: bool
    arguments: ToolArgumentContract
    observation: ToolObservationContract
    freshness: ToolFreshnessContract
    audit: ToolAuditContract

    @model_validator(mode="after")
    def validate_permission_boundary(self) -> Self:
        if self.parallel_safe and (
            self.mode != "read" or self.side_effects != "none"
        ):
            raise ValueError("only side-effect-free read tools may be parallel safe")
        if self.mode == "read" and self.side_effects != "none":
            raise ValueError("read tools must declare no side effects")
        return self


class ConditionalEvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=80,
    )
    primary_tool_id: str = Field(min_length=1, max_length=100)
    fallback_tool_id: str = Field(min_length=1, max_length=100)
    fallback_trigger: Literal["on_error", "on_not_found"]
    fallback_arguments: dict[str, Any] = Field(default_factory=dict)
    speculative_parallel_allowed: bool = False

    @model_validator(mode="after")
    def validate_direction(self) -> Self:
        if self.primary_tool_id == self.fallback_tool_id:
            raise ValueError("primary and fallback tools must be different")
        if self.speculative_parallel_allowed:
            raise ValueError("conditional evidence tools cannot be prefetched")
        return self


class ToolRegistryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_version: str = Field(min_length=1, max_length=40)
    status: Literal["design_only", "shadow", "active"]
    tools: tuple[ToolRegistryEntry, ...] = Field(min_length=1, max_length=100)
    conditional_evidence: tuple[ConditionalEvidenceContract, ...] = Field(
        default=(),
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_registry_references(self) -> Self:
        tool_ids = [item.tool_id for item in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool ids must be unique")
        langchain_names = [item.langchain_name for item in self.tools]
        if len(langchain_names) != len(set(langchain_names)):
            raise ValueError("LangChain tool names must be unique")
        group_ids = [item.group_id for item in self.conditional_evidence]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("conditional evidence group ids must be unique")

        known_tool_ids = set(tool_ids)
        for group in self.conditional_evidence:
            if not {
                group.primary_tool_id,
                group.fallback_tool_id,
            }.issubset(known_tool_ids):
                raise ValueError(
                    "conditional evidence groups must reference registered tools"
                )
        return self
