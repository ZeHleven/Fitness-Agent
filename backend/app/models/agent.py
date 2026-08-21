import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_clarification: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_agent_run_user_idempotency",
        ),
        Index(
            "uq_agent_runs_one_running_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN "
            "('direct', 'planned', 'clarify', 'safe_stop')",
            name="ck_agent_runs_execution_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running", server_default="running", index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    primary_intent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resolved_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    expanded_intents: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    subtasks: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    missing_slots: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    tool_allowlist: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    execution_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    execution_trace: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    risk_level: Mapped[str] = mapped_column(
        String(20), default="low", server_default="low"
    )
    clarification_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    understanding_version: Mapped[str | None] = mapped_column(
        String(20), default="v2", nullable=True
    )
    intent_source: Mapped[str] = mapped_column(
        String(20), default="rules", server_default="rules"
    )
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    intent_fallback_reason: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    intent_error_category: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        Index(
            "uq_agent_messages_one_assistant_per_run",
            "run_id",
            unique=True,
            postgresql_where=text(
                "role = 'assistant' AND run_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    content_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        Index(
            "uq_agent_tool_calls_run_call_id",
            "run_id",
            "call_id",
            unique=True,
            postgresql_where=text("call_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    result_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="completed", server_default="completed"
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    proposal_type: Mapped[str] = mapped_column(String(60), index=True)
    payload_data: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_key", name="uq_agent_memory_user_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    memory_key: Mapped[str] = mapped_column(String(120))
    value_data: Mapped[dict] = mapped_column(JSONB)
    source_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
