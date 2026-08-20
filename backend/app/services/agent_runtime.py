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
from app.models.agent import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from app.services.agent_intent import IntentResolution, route_tools
from app.services.agent_intent_model import resolve_intent_with_fallback
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


class AgentRunOwnershipLost(RuntimeError):
    """Raised when a stale worker attempt tries to persist a run result."""


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
) -> None:
    await db.rollback()
    filters = [
        AgentRun.id == run_id,
        AgentRun.status == "running",
    ]
    if expected_attempt_count is not None:
        filters.append(AgentRun.attempt_count == expected_attempt_count)
    result = await db.execute(
        update(AgentRun)
        .where(*filters)
        .values(
            status="failed",
            error_code=error_code,
            completed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            lease_expires_at=None,
        )
    )
    if result.rowcount != 1 and expected_attempt_count is not None:
        await db.rollback()
        raise AgentRunOwnershipLost(
            f"Agent run ownership lost while failing: run_id={run_id} "
            f"attempt={expected_attempt_count}"
        )
    await db.commit()


def _build_model() -> ChatOpenAI:
    if not settings.AGENT_ENABLED:
        raise AIServiceError("Agent 服务尚未启用")
    if not settings.DEEPSEEK_API_KEY:
        raise AIServiceError("AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY")
    return ChatOpenAI(
        model=settings.AGENT_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL.rstrip("/"),
        temperature=0.2,
        timeout=settings.AGENT_TIMEOUT_SECONDS,
        max_retries=1,
        use_responses_api=False,
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
) -> dict[str, Any]:
    model = _build_model()
    tools = build_read_tools(
        db,
        user_id=user_id,
        allowlist=tool_allowlist,
    )
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
    try:
        existing = await db.scalar(
            select(AgentMessage).where(
                AgentMessage.run_id == run.id,
                AgentMessage.role == "assistant",
            )
        )
        if existing is not None:
            cards = existing.content_data.get("cards", [])
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
        tool_allowlist = route_tools(resolution)
        run.status = "running"
        run.primary_intent = resolution.primary_intent
        run.resolved_query = resolution.resolved_query
        run.references = [item.model_dump() for item in resolution.references]
        run.expanded_intents = resolution.expanded_intents
        run.subtasks = resolution.subtasks
        run.missing_slots = resolution.missing_slots
        run.tool_allowlist = tool_allowlist
        run.risk_level = resolution.risk_level
        run.clarification_required = resolution.clarification_required
        run.clarification_question = resolution.clarification_question
        run.understanding_version = "v2"
        run.intent_source = intent_outcome.source
        run.intent_confidence = resolution.confidence
        run.intent_attempt_count = intent_outcome.attempt_count
        run.intent_fallback_reason = intent_outcome.fallback_reason
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
            conversation.updated_at = now
            await db.commit()
            return AgentRuntimeResult(reply=reply, run_id=run.id)

        result = await invoke_langchain_agent(
            db,
            user_id=run.user_id,
            history=history,
            user_message=user_message,
            tool_allowlist=tool_allowlist,
            resolved_query=resolution.resolved_query,
            subtasks=resolution.subtasks,
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
        conversation.updated_at = now
        await db.commit()
        return AgentRuntimeResult(reply=reply, run_id=run.id, cards=cards)
    except AgentRunOwnershipLost:
        await db.rollback()
        raise
    except AIServiceError as exc:
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="ai_service_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        raise
    except Exception as exc:
        logger.exception("Agent run failed: run_id=%s", run.id)
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="agent_runtime_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
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
