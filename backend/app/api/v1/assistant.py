from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from app.ai.assistant import NOT_CONFIGURED, TIMED_OUT, UNAVAILABLE, chat_messages, system_prompt
from app.api.deps import AssistantContexts, AssistantLLM
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.domain.ai import AssistantReply, AssistantRequest

router = APIRouter(tags=["assistant"])
logger = logging.getLogger("app.api.assistant")


@router.post(
    "/assistant/messages",
    response_model=AssistantReply,
    summary="Ask the planning assistant about the open project (503 when AI is not configured)",
)
async def ask_assistant(
    body: AssistantRequest, request: Request, llm: AssistantLLM, contexts: AssistantContexts
) -> AssistantReply:
    if not llm.configured:
        raise ServiceUnavailableError(NOT_CONFIGURED)
    context = None
    if body.project_id is not None:
        context = await contexts.context(body.project_id)
        if context is None:
            raise NotFoundError("Project not found")

    timeout_s: float = request.app.state.settings.assistant_timeout_s
    try:
        reply, usage = await asyncio.wait_for(
            asyncio.to_thread(
                llm.text,
                system=system_prompt(body.page, context),
                messages=chat_messages([(m.role, m.content) for m in body.messages]),
                timeout_s=timeout_s,
            ),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise ServiceUnavailableError(TIMED_OUT) from exc
    except Exception as exc:
        logger.exception("assistant call failed")
        raise ServiceUnavailableError(UNAVAILABLE) from exc
    if not reply:
        raise ServiceUnavailableError(UNAVAILABLE)
    logger.info(
        "assistant replied",
        extra={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    )
    return AssistantReply(reply=reply)
