from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_trace import AgentExecutionTrace, ExecutionMode


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)


class AgentRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)
    client_request_id: str = Field(min_length=8, max_length=120)


class AgentRunCreateResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    poll_after_ms: int = 800


class AgentCard(BaseModel):
    type: str = Field(min_length=1, max_length=60)
    data: dict[str, Any] = Field(default_factory=dict)


class AgentChatResponse(BaseModel):
    reply: str
    conversation_id: str
    run_id: str
    cards: list[AgentCard] = Field(default_factory=list)


class AgentConversationResponse(BaseModel):
    id: str
    title: str | None
    summary: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class AgentMessageResponse(BaseModel):
    id: str
    run_id: str | None
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    content_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: str
    status: str
    primary_intent: str | None
    resolved_query: str | None
    references: list[dict[str, Any]] = Field(default_factory=list)
    expanded_intents: list[str] = Field(default_factory=list)
    subtasks: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)
    execution_mode: ExecutionMode | None
    execution_trace: AgentExecutionTrace | None
    risk_level: str
    clarification_required: bool
    clarification_question: str | None
    understanding_version: str | None
    intent_source: str
    intent_confidence: float | None
    intent_attempt_count: int
    intent_fallback_reason: str | None
    intent_error_category: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None
    error_message: str | None = None
    reply: str | None = None
    cards: list[AgentCard] = Field(default_factory=list)
    poll_after_ms: int | None = None
    queued_at: datetime
    processing_started_at: datetime | None
    attempt_count: int
    started_at: datetime
    completed_at: datetime | None
    model_config = {"from_attributes": True}
