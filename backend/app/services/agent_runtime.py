from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.agent import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from app.schemas.agent import AgentProposalReference
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.agent_controller import (
    ToolAuditEvent,
    execute_planned_agent,
)
from app.services.agent_intent import IntentResolution, route_tools
from app.services.agent_intent_model import resolve_intent_with_fallback
from app.services.agent_plan_adjustment_proposal_persistence import (
    PlanAdjustmentProposalPersistenceRejected,
    persist_optional_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposal_trace import (
    attach_proposal_creation_decision,
    mark_proposal_persistence_failed,
    mark_proposal_persistence_rejected,
    mark_proposal_persisted,
)
from app.services.agent_plan_adjustment_proposals import (
    build_runtime_plan_adjustment_proposal,
)
from app.services.agent_structured_errors import safe_error_category
from app.services.agent_tool_registry_shadow_metric_adapter import (
    emit_registry_shadow_metrics,
)
from app.services.agent_tool_registry_read_enforcement import (
    apply_optional_registry_read_enforcement,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
    attach_registry_shadow_report,
    create_registry_shadow_session,
)
from app.services.agent_trace import (
    add_stage_timing,
    build_initial_execution_trace,
    complete_execution_trace,
    terminate_execution_trace,
)
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME, build_read_tools
from app.services.ai_client import AIServiceError


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是 Fitness Agent，一位中文健身对话助手。

能力边界：
- 一般健身知识可以直接回答；用户自己的资料、计划、训练和进度必须以工具结果为准。
- 当前只允许查询，不能声称已经开始训练、记录训练组、完成训练、修改资料或保存计划。
- 工具无结果时明确说明，不得编造用户数据。
- 简单问题使用简洁文本；有结构化训练数据时先回答结论，再概括关键数据。

健康安全：
- 不进行医疗诊断、处方或治疗承诺。
- 出现胸痛、呼吸困难、晕厥、严重或急性疼痛等红旗信息时，优先建议停止训练并及时寻求专业医疗帮助。
- 有疼痛或伤病时，不建议盲目加量；先说明训练边界和不确定性。
"""


@dataclass(frozen=True)
class AgentRuntimeResult:
    reply: str
    run_id: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    execution_trace: AgentExecutionTrace | None = None
    proposal: AgentProposalReference | None = None


class AgentRunOwnershipLost(RuntimeError):
    """Raised when a stale worker attempt tries to persist a run result."""


def _proposal_reference_from_data(
    value: Any,
) -> AgentProposalReference | None:
    if not isinstance(value, dict):
        return None
    try:
        return AgentProposalReference.model_validate(value)
    except ValueError:
        return None


def _proposal_reference_from_model(proposal: Any) -> AgentProposalReference:
    return AgentProposalReference(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        expires_at=proposal.expires_at,
        payload_fingerprint=proposal.payload_fingerprint,
    )


def _finalize_registry_shadow_trace(
    trace: AgentExecutionTrace,
    session: ToolRegistryShadowSession | None,
) -> AgentExecutionTrace:
    if session is None:
        return trace
    try:
        if trace.observations:
            session.record_final_observations(trace)
        report = session.build_report()
    except Exception:  # pragma: no cover - report loss must not fail v1
        return trace
    try:
        emit_registry_shadow_metrics(
            report,
            enabled=(
                settings.AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS
            ),
        )
    except Exception:  # pragma: no cover - defensive adapter boundary
        pass
    return attach_registry_shadow_report(
        trace,
        report,
        persist_trace=(
            settings.AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE
        ),
    )


async def _lock_run_ownership(
    db: AsyncSession,
    *,
    run_id: str,
    expected_attempt_count: int | None,
) -> None:
    if expected_attempt_count is None:
        return
    with db.no_autoflush:
        owned_run_id = await db.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
                AgentRun.attempt_count == expected_attempt_count,
            )
            .with_for_update()
        )
    if owned_run_id is None:
        raise AgentRunOwnershipLost(
            f"Agent run ownership lost: run_id={run_id} "
            f"attempt={expected_attempt_count}"
        )


async def _mark_owned_run_failed(
    db: AsyncSession,
    *,
    run_id: str,
    expected_attempt_count: int | None,
    error_code: str,
    duration_ms: int,
    execution_trace: AgentExecutionTrace | None = None,
) -> None:
    await db.rollback()
    filters = [
        AgentRun.id == run_id,
        AgentRun.status == "running",
    ]
    if expected_attempt_count is not None:
        filters.append(AgentRun.attempt_count == expected_attempt_count)
    values: dict[str, Any] = {
        "status": "failed",
        "error_code": error_code,
        "completed_at": datetime.now(timezone.utc),
        "duration_ms": duration_ms,
        "lease_expires_at": None,
    }
    if execution_trace is not None:
        values["execution_mode"] = execution_trace.execution_mode
        values["execution_trace"] = execution_trace.model_dump(mode="json")
    result = await db.execute(
        update(AgentRun)
        .where(*filters)
        .values(**values)
    )
    if result.rowcount != 1 and expected_attempt_count is not None:
        await db.rollback()
        raise AgentRunOwnershipLost(
            f"Agent run ownership lost while failing: run_id={run_id} "
            f"attempt={expected_attempt_count}"
        )
    await db.commit()


def _build_model(
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    if not settings.AGENT_ENABLED:
        raise AIServiceError("Agent 服务尚未启用")
    if not settings.DEEPSEEK_API_KEY:
        raise AIServiceError("AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY")
    model_options: dict[str, Any] = {
        "model": settings.AGENT_MODEL,
        "api_key": settings.DEEPSEEK_API_KEY,
        "base_url": settings.DEEPSEEK_BASE_URL.rstrip("/"),
        "temperature": temperature,
        "timeout": settings.AGENT_TIMEOUT_SECONDS,
        "max_retries": 1,
        "use_responses_api": False,
    }
    if max_tokens is not None:
        model_options["max_tokens"] = max_tokens
    return ChatOpenAI(
        **model_options,
    )


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                value = block.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
        return "\n".join(item for item in text_parts if item).strip()
    return ""


def _json_result(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {"items": content}
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return {"content": content}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": str(content)}


def _audit_result_summary(tool_id: str, content: Any) -> dict[str, Any]:
    result = _json_result(content)
    if tool_id == "profile.get_summary":
        return {
            "found": bool(result.get("found")),
            "fields_returned": sorted(
                key for key, value in result.items()
                if key != "found" and value is not None
            ),
        }
    if tool_id == "health.get_screening_summary":
        injuries = result.get("injuries")
        chronic = result.get("chronic_conditions")
        return {
            "found": bool(result.get("found")),
            "injury_count": len(injuries) if isinstance(injuries, list) else 0,
            "chronic_condition_count": len(chronic) if isinstance(chronic, list) else 0,
            "screening_completed": bool(result.get("screening_completed")),
        }
    if tool_id == "plan.get_active":
        plan = result.get("plan")
        return {
            "found": bool(result.get("found")),
            "plan_id": plan.get("id") if isinstance(plan, dict) else None,
            "exercise_count": (
                len(plan.get("exercises", [])) if isinstance(plan, dict) else 0
            ),
        }
    if tool_id == "workout.get_next":
        exercises = result.get("exercises")
        return {
            "found": bool(result.get("found")),
            "plan_id": result.get("plan_id"),
            "day_of_week": result.get("day_of_week"),
            "exercise_count": len(exercises) if isinstance(exercises, list) else 0,
        }
    if tool_id == "workout.get_active_session":
        session = result.get("session")
        return {
            "found": bool(result.get("found")),
            "session_id": session.get("id") if isinstance(session, dict) else None,
            "exercise_count": (
                len(session.get("exercises", [])) if isinstance(session, dict) else 0
            ),
        }
    if tool_id == "workout.list_history":
        return {"count": result.get("count", 0)}
    if tool_id == "workout.get_progress":
        return {
            key: result.get(key)
            for key in (
                "weeks",
                "total_sessions",
                "total_sets",
                "total_reps",
                "total_volume_kg",
            )
        }
    return {"keys_returned": sorted(result)}


def _extract_agent_output(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    messages = result.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AIServiceError("Agent 未返回有效内容")
    reply = _message_content_text(getattr(messages[-1], "content", ""))
    if not reply:
        raise AIServiceError("Agent 未返回有效内容")

    cards: list[dict[str, Any]] = []
    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        tool_name = getattr(message, "name", None)
        canonical_id = TOOL_ID_BY_LANGCHAIN_NAME.get(tool_name)
        if canonical_id:
            cards.append({
                "type": canonical_id,
                "data": _json_result(getattr(message, "content", "")),
            })
    return reply, cards


def _tool_call_records(result: dict[str, Any], run_id: str) -> list[AgentToolCall]:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return []
    outputs: dict[str, Any] = {}
    for message in messages:
        if getattr(message, "type", None) == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if isinstance(call_id, str):
                outputs[call_id] = getattr(message, "content", "")

    records: list[AgentToolCall] = []
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id") if isinstance(call.get("id"), str) else None
            langchain_name = call.get("name")
            if not isinstance(langchain_name, str):
                continue
            canonical_id = TOOL_ID_BY_LANGCHAIN_NAME.get(
                langchain_name, langchain_name
            )
            arguments = call.get("args")
            records.append(AgentToolCall(
                run_id=run_id,
                call_id=call_id,
                tool_name=canonical_id,
                arguments_data=(
                    arguments if isinstance(arguments, dict) else {"value": arguments}
                ),
                result_data=_audit_result_summary(
                    canonical_id,
                    outputs.get(call_id, ""),
                ),
                status="completed",
            ))
    return records


def _usage_totals(result: dict[str, Any]) -> tuple[int | None, int | None]:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None, None
    input_tokens = 0
    output_tokens = 0
    found = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, dict):
            continue
        found = True
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return (input_tokens, output_tokens) if found else (None, None)


def _clarification_reply(resolution: IntentResolution) -> str:
    if resolution.clarification_question:
        return resolution.clarification_question
    if resolution.missing_slots:
        fields = "、".join(resolution.missing_slots)
        return f"为了准确回答，我还需要确认：{fields}。请补充后我再继续。"
    return "为了准确理解你的目标，请再补充一下你希望我查询或比较的具体内容。"


HIGH_RISK_REPLY = (
    "你描述的情况可能属于需要优先处理的健康警示。请立即停止训练，不要继续加量或硬撑；"
    "如果正在出现胸痛、呼吸困难、晕厥或严重急性疼痛，请及时联系急救或尽快就医。"
    "我不能在小程序里替代医生诊断。"
)


async def _load_history(
    db: AsyncSession,
    *,
    conversation_id: str,
    before_run: AgentRun,
) -> list[dict[str, str]]:
    history_run = aliased(AgentRun)
    filters = [
        AgentMessage.conversation_id == conversation_id,
        AgentMessage.role.in_(("user", "assistant")),
        or_(
            and_(
                AgentMessage.run_id.is_(None),
                AgentMessage.created_at <= before_run.queued_at,
            ),
            history_run.queued_at < before_run.queued_at,
            and_(
                history_run.queued_at == before_run.queued_at,
                history_run.id < before_run.id,
            ),
        ),
    ]
    rows = list((await db.execute(
        select(AgentMessage)
        .outerjoin(history_run, AgentMessage.run_id == history_run.id)
        .where(*filters)
        .order_by(AgentMessage.created_at.desc())
        .limit(settings.AGENT_MAX_HISTORY_MESSAGES)
    )).scalars().all())
    rows.reverse()
    return [{"role": item.role, "content": item.content} for item in rows]


async def invoke_langchain_agent(
    db: AsyncSession,
    *,
    user_id: str,
    history: list[dict[str, str]],
    user_message: str,
    tool_allowlist: list[str],
    resolved_query: str | None = None,
    subtasks: list[str] | None = None,
    shadow_session: ToolRegistryShadowSession | None = None,
) -> dict[str, Any]:
    model = _build_model()
    tools = build_read_tools(
        db,
        user_id=user_id,
        allowlist=tool_allowlist,
    )
    if shadow_session is not None:
        shadow_session.record_constructed_tools(tools, tool_allowlist)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        name="fitness_agent_v1",
    )
    execution_message = user_message
    normalized_query = (resolved_query or "").strip()
    normalized_subtasks = [item.strip() for item in (subtasks or []) if item.strip()]
    if normalized_query and (
        normalized_query != user_message.strip() or normalized_subtasks
    ):
        execution_message = (
            f"用户原始表达：{user_message}\n"
            f"经服务端对话理解层消解后的请求：{normalized_query}\n"
        )
        if normalized_subtasks:
            execution_message += (
                "本轮需要覆盖的语义目标："
                f"{'；'.join(normalized_subtasks)}\n"
            )
        execution_message += "请围绕消解后的请求完成回答，不要扩展到无关目标。"
    return await agent.ainvoke(
        {"messages": [*history, {"role": "user", "content": execution_message}]},
        config={"recursion_limit": settings.AGENT_RECURSION_LIMIT},
    )


async def execute_agent_run(
    db: AsyncSession,
    *,
    run: AgentRun,
    conversation: AgentConversation,
    user_message: str,
    expected_attempt_count: int | None = None,
) -> AgentRuntimeResult:
    started = time.perf_counter()
    execution_trace: AgentExecutionTrace | None = None
    shadow_session: ToolRegistryShadowSession | None = None
    try:
        existing = await db.scalar(
            select(AgentMessage).where(
                AgentMessage.run_id == run.id,
                AgentMessage.role == "assistant",
            )
        )
        if existing is not None:
            cards = existing.content_data.get("cards", [])
            proposal_reference = _proposal_reference_from_data(
                existing.content_data.get("proposal")
            )
            if run.execution_trace:
                execution_trace = AgentExecutionTrace.model_validate(
                    run.execution_trace
                )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            run.status = "completed"
            run.completed_at = run.completed_at or datetime.now(timezone.utc)
            run.lease_expires_at = None
            await db.commit()
            return AgentRuntimeResult(
                reply=existing.content,
                run_id=run.id,
                cards=cards if isinstance(cards, list) else [],
                execution_trace=execution_trace,
                proposal=proposal_reference,
            )

        shadow_session = create_registry_shadow_session(
            run_id=run.id,
            enabled=settings.AGENT_TOOL_REGISTRY_SHADOW_ENABLED,
            sample_rate=settings.AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE,
        )
        history = await _load_history(
            db,
            conversation_id=conversation.id,
            before_run=run,
        )
        intent_outcome = await resolve_intent_with_fallback(
            user_message,
            context_messages=history,
            pending_clarification=(conversation.pending_clarification or None),
        )
        resolution = intent_outcome.resolution
        legacy_tool_allowlist = route_tools(resolution)
        if shadow_session is not None:
            shadow_session.record_route(resolution, legacy_tool_allowlist)
        enforcement = apply_optional_registry_read_enforcement(
            resolution=resolution,
            legacy_tool_ids=legacy_tool_allowlist,
            enabled=settings.AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED,
            run_id=run.id,
        )
        tool_allowlist = list(enforcement.tool_allowlist)
        execution_trace = build_initial_execution_trace(
            resolution,
            tool_allowlist,
            intent_outcome,
        )
        run.status = "running"
        run.primary_intent = resolution.primary_intent
        run.resolved_query = resolution.resolved_query
        run.references = [item.model_dump() for item in resolution.references]
        run.expanded_intents = resolution.expanded_intents
        run.subtasks = resolution.subtasks
        run.missing_slots = resolution.missing_slots
        run.tool_allowlist = tool_allowlist
        run.execution_mode = execution_trace.execution_mode
        run.execution_trace = execution_trace.model_dump(mode="json")
        run.risk_level = resolution.risk_level
        run.clarification_required = resolution.clarification_required
        run.clarification_question = resolution.clarification_question
        run.understanding_version = "v2"
        run.intent_source = intent_outcome.source
        run.intent_confidence = resolution.confidence
        run.intent_attempt_count = intent_outcome.attempt_count
        run.intent_fallback_reason = intent_outcome.fallback_reason
        run.intent_error_category = intent_outcome.error_category
        run.model_name = settings.AGENT_MODEL

        if resolution.risk_level == "high":
            conversation.pending_clarification = {}
        elif resolution.clarification_required:
            conversation.pending_clarification = {
                "origin_run_id": run.id,
                "original_message": user_message,
                "resolved_query": resolution.resolved_query,
                "references": [
                    item.model_dump() for item in resolution.references
                ],
                "primary_intent": resolution.primary_intent,
                "expanded_intents": resolution.expanded_intents,
                "subtasks": resolution.subtasks,
                "missing_slots": resolution.missing_slots,
                "clarification_question": resolution.clarification_question,
                "risk_level": resolution.risk_level,
                "confidence": resolution.confidence,
                "understanding_version": "v2",
            }
        else:
            conversation.pending_clarification = {}
        await _lock_run_ownership(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
        )
        await db.commit()

        if resolution.risk_level == "high" or resolution.clarification_required:
            reply = (
                HIGH_RISK_REPLY
                if resolution.risk_level == "high"
                else _clarification_reply(resolution)
            )
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action=(
                    "safe_stop"
                    if resolution.risk_level == "high"
                    else "clarify"
                ),
                termination_reason=(
                    "health_red_flag"
                    if resolution.risk_level == "high"
                    else "clarification_required"
                ),
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            db.add(AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content=reply,
                content_data={
                    "guardrail": (
                        "health_red_flag"
                        if resolution.risk_level == "high"
                        else "clarification_required"
                    ),
                    "missing_slots": resolution.missing_slots,
                },
            ))
            now = datetime.now(timezone.utc)
            run.status = "completed"
            run.completed_at = now
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.lease_expires_at = None
            run.execution_trace = execution_trace.model_dump(mode="json")
            conversation.updated_at = now
            await db.commit()
            return AgentRuntimeResult(
                reply=reply,
                run_id=run.id,
                execution_trace=execution_trace,
            )

        if (
            execution_trace.execution_mode == "planned"
            and settings.AGENT_PLANNED_EXECUTION_ENABLED
        ):
            async def invoke_parallel_read_tool(
                tool_id: str,
                arguments: dict[str, Any],
            ) -> Any:
                # SQLAlchemy AsyncSession is not safe for concurrent tasks.
                # Every Planner-owned parallel read therefore gets an isolated,
                # short-lived session while identity remains server-owned.
                async with AsyncSessionLocal() as parallel_db:
                    parallel_tools = build_read_tools(
                        parallel_db,
                        user_id=run.user_id,
                        allowlist=[tool_id],
                    )
                    return await parallel_tools[0].ainvoke(arguments)

            async def persist_planned_event(
                trace: AgentExecutionTrace,
                audit: ToolAuditEvent | None,
            ) -> None:
                nonlocal execution_trace
                execution_trace = trace
                await _lock_run_ownership(
                    db,
                    run_id=run.id,
                    expected_attempt_count=expected_attempt_count,
                )
                run.execution_trace = trace.model_dump(mode="json")
                if audit is not None:
                    existing_audit = await db.scalar(
                        select(AgentToolCall.id).where(
                            AgentToolCall.run_id == run.id,
                            AgentToolCall.call_id == audit.call_id,
                        )
                    )
                    if existing_audit is None:
                        db.add(AgentToolCall(
                            run_id=run.id,
                            call_id=audit.call_id,
                            tool_name=audit.tool_id,
                            arguments_data=audit.arguments,
                            result_data=audit.result_summary,
                            status=audit.status,
                            error_code=audit.error_code,
                            duration_ms=audit.duration_ms,
                        ))
                await db.commit()

            planned_result = await execute_planned_agent(
                db=db,
                user_id=run.user_id,
                run_id=run.id,
                model=_build_model(
                    temperature=0,
                    max_tokens=settings.AGENT_PLANNING_MAX_TOKENS,
                ),
                goal=resolution.resolved_query,
                subtasks=resolution.subtasks,
                tool_allowlist=tool_allowlist,
                initial_trace=execution_trace,
                summarize_observation=_audit_result_summary,
                event_sink=persist_planned_event,
                parallel_tool_invoker=invoke_parallel_read_tool,
                shadow_session=shadow_session,
                proposal_creation_enabled=(
                    settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
                ),
            )
            execution_trace = planned_result.execution_trace
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
            reply = planned_result.reply
            cards = planned_result.cards
            run.input_tokens = planned_result.input_tokens
            run.output_tokens = planned_result.output_tokens

            if execution_trace.terminal_action == "clarify":
                run.clarification_required = True
                run.clarification_question = reply
                run.missing_slots = planned_result.missing_slots
                conversation.pending_clarification = {
                    "origin_run_id": run.id,
                    "original_message": user_message,
                    "resolved_query": resolution.resolved_query,
                    "references": [
                        item.model_dump() for item in resolution.references
                    ],
                    "primary_intent": resolution.primary_intent,
                    "expanded_intents": resolution.expanded_intents,
                    "subtasks": resolution.subtasks,
                    "missing_slots": planned_result.missing_slots,
                    "clarification_question": reply,
                    "risk_level": resolution.risk_level,
                    "confidence": resolution.confidence,
                    "understanding_version": "v2",
                }
            elif execution_trace.terminal_action == "safe_stop":
                run.risk_level = "high"
                conversation.pending_clarification = {}

            proposal_reference: AgentProposalReference | None = None
            runtime_proposal = None
            if settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED:
                runtime_proposal = build_runtime_plan_adjustment_proposal(
                    feature_enabled=True,
                    run_owned=True,
                    selected_outcome=(
                        execution_trace.finalization_contract.selected_outcome
                        if execution_trace.finalization_contract is not None
                        and execution_trace.finalization_contract.selected_outcome
                        is not None
                        else ""
                    ),
                    terminal_action=execution_trace.terminal_action or "",
                    intent_allows_adjustment=(
                        execution_trace.finalization_contract is not None
                        and "adjustment_proposal"
                        in execution_trace.finalization_contract.allowed_outcomes
                    ),
                    risk_level=run.risk_level,
                    clarification_required=run.clarification_required,
                    observations=planned_result.proposal_observations,
                    proposal_draft=planned_result.proposal_draft,
                    created_at=datetime.now(timezone.utc),
                )
                execution_trace = attach_proposal_creation_decision(
                    execution_trace,
                    runtime_proposal.decision,
                )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            if runtime_proposal is not None and runtime_proposal.built is not None:
                try:
                    persisted = await persist_optional_plan_adjustment_proposal(
                        db,
                        enabled=(
                            settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
                        ),
                        user_id=run.user_id,
                        conversation_id=conversation.id,
                        run_id=run.id,
                        expected_attempt_count=(
                            expected_attempt_count
                            if expected_attempt_count is not None
                            else run.attempt_count
                        ),
                        built=runtime_proposal.built,
                    )
                except PlanAdjustmentProposalPersistenceRejected as exc:
                    execution_trace = mark_proposal_persistence_rejected(
                        execution_trace,
                        reason_code=exc.reason_code,
                    )
                    raise
                except Exception:
                    execution_trace = mark_proposal_persistence_failed(
                        execution_trace
                    )
                    raise
                if persisted.proposal is not None:
                    execution_trace = mark_proposal_persisted(
                        execution_trace,
                        created=persisted.created,
                    )
                    proposal_reference = _proposal_reference_from_model(
                        persisted.proposal
                    )
                elif persisted.reason_code is not None:
                    execution_trace = mark_proposal_persistence_rejected(
                        execution_trace,
                        reason_code=persisted.reason_code,
                    )
                else:  # pragma: no cover - persistence result invariant
                    execution_trace = mark_proposal_persistence_failed(
                        execution_trace
                    )
                    raise RuntimeError(
                        "proposal persistence returned no proposal or reason"
                    )
            message_content_data: dict[str, Any] = {}
            if cards:
                message_content_data["cards"] = cards
            if proposal_reference is not None:
                message_content_data["proposal"] = (
                    proposal_reference.model_dump(mode="json")
                )
            db.add(AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content=reply,
                content_data=message_content_data,
            ))
            now = datetime.now(timezone.utc)
            run.status = "completed"
            run.completed_at = now
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.lease_expires_at = None
            run.execution_trace = execution_trace.model_dump(mode="json")
            conversation.updated_at = now
            await db.commit()
            return AgentRuntimeResult(
                reply=reply,
                run_id=run.id,
                cards=cards,
                execution_trace=execution_trace,
                proposal=proposal_reference,
            )

        direct_started = time.perf_counter()
        try:
            result = await invoke_langchain_agent(
                db,
                user_id=run.user_id,
                history=history,
                user_message=user_message,
                tool_allowlist=tool_allowlist,
                resolved_query=resolution.resolved_query,
                subtasks=resolution.subtasks,
                shadow_session=shadow_session,
            )
        except Exception as exc:
            execution_trace = add_stage_timing(
                execution_trace,
                stage="direct_agent",
                source="model",
                status="error",
                latency_ms=round(
                    (time.perf_counter() - direct_started) * 1000
                ),
                error_category=safe_error_category(exc),
            )
            raise
        execution_trace = add_stage_timing(
            execution_trace,
            stage="direct_agent",
            source="model",
            status="success",
            latency_ms=round(
                (time.perf_counter() - direct_started) * 1000
            ),
        )
        execution_trace = complete_execution_trace(
            execution_trace,
            result,
            summarize_observation=_audit_result_summary,
        )
        execution_trace = _finalize_registry_shadow_trace(
            execution_trace,
            shadow_session,
        )
        reply, cards = _extract_agent_output(result)
        await _lock_run_ownership(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
        )
        db.add(AgentMessage(
            conversation_id=conversation.id,
            run_id=run.id,
            role="assistant",
            content=reply,
            content_data={"cards": cards} if cards else {},
        ))
        for record in _tool_call_records(result, run.id):
            db.add(record)
        run.input_tokens, run.output_tokens = _usage_totals(result)
        now = datetime.now(timezone.utc)
        run.status = "completed"
        run.completed_at = now
        run.duration_ms = round((time.perf_counter() - started) * 1000)
        run.lease_expires_at = None
        run.execution_trace = execution_trace.model_dump(mode="json")
        conversation.updated_at = now
        await db.commit()
        return AgentRuntimeResult(
            reply=reply,
            run_id=run.id,
            cards=cards,
            execution_trace=execution_trace,
        )
    except AgentRunOwnershipLost:
        await db.rollback()
        raise
    except AIServiceError as exc:
        if execution_trace is not None:
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action="failed",
                termination_reason="ai_service_error",
                failed=True,
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="ai_service_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
            execution_trace=execution_trace,
        )
        raise
    except Exception as exc:
        logger.exception("Agent run failed: run_id=%s", run.id)
        if execution_trace is not None:
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action="failed",
                termination_reason="agent_runtime_error",
                failed=True,
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="agent_runtime_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
            execution_trace=execution_trace,
        )
        raise AIServiceError("Agent 暂时无法完成请求，请稍后重试") from exc


async def run_agent_chat(
    db: AsyncSession,
    *,
    user_id: str,
    conversation: AgentConversation,
    user_message: str,
) -> AgentRuntimeResult:
    """Compatibility path for existing HTTP clients and focused tests.

    Production miniapp traffic uses the durable queue endpoint. Keeping this
    synchronous wrapper avoids breaking older clients during the rollout.
    """
    now = datetime.now(timezone.utc)
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user_id,
        status="running",
        model_name=settings.AGENT_MODEL,
        processing_started_at=now,
        attempt_count=1,
    )
    db.add(run)
    await db.flush()
    db.add(AgentMessage(
        conversation_id=conversation.id,
        run_id=run.id,
        role="user",
        content=user_message,
    ))
    await db.commit()
    return await execute_agent_run(
        db,
        run=run,
        conversation=conversation,
        user_message=user_message,
    )
