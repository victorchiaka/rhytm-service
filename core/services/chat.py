import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.ai.base import (
    BaseAIPlatform,
    Message,
    StreamChunk,
    ToolCall,
    ToolContext,
    ToolResult,
)
from core.ai.context import build_user_context
from core.ai.gemini import Gemini
from core.ai.prompts import TITLE_PROMPT, render_system_prompt
from core.ai.tool_calling import TOOLS, execute_tool
from core.messages import CHAT_MESSAGES
from core.models.chat import ChatMessage, Conversation
from core.schemas.chat import (
    ChatMessageResponse,
    ConversationDetailResponse,
    ConversationResponse,
    DeleteConversationResponse,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.5-flash-lite"
HISTORY_LIMIT = 40  # most recent messages replayed to the model
MAX_TOOL_TURNS = 4  # tool -> result -> tool ... safety net


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _tool_turn(calls: list[ToolCall], results: list[dict]) -> list[Message]:
    """The model's calls and our responses, as one assistant/user exchange."""
    return [
        Message(role="model", tool_calls=calls),
        Message(
            role="tool",
            tool_results=[
                ToolResult(call.name, result) for call, result in zip(calls, results)
            ],
        ),
    ]


def _tool_log(calls: list[ToolCall], results: list[dict]) -> list[dict]:
    return [
        {"name": call.name, "args": call.args, "ok": "error" not in result}
        for call, result in zip(calls, results)
    ]


def _clean_title(raw: str) -> str | None:
    """Keep what the model produced on one line, unquoted and short."""
    title = " ".join(raw.split()).strip("\"'“”‘’.,:;-")
    return title[:60] or None


def _fallback_title(messages: list[Message]) -> str:
    opening = next((m.content for m in messages if m.role == "user"), "")
    return opening[:60].strip() or "New conversation"


class ChatService:
    """Streams assistant replies through a model-agnostic AI platform.

    Swap providers by passing any BaseAIPlatform implementation:
        ChatService(platform=SomeOtherProvider(...))
    """

    def __init__(self, platform: BaseAIPlatform | None = None):
        self._platform = platform

    @property
    def platform(self) -> BaseAIPlatform:
        if self._platform is None:
            self._platform = Gemini(model=GEMINI_MODEL)
        return self._platform

    async def prepare(
        self, payload: SendMessageRequest, user_id: str, db: AsyncSession
    ) -> UUID:
        """Validates the payload, resolves the conversation and stores the user message."""
        content = payload.content.strip()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Message content cannot be empty.",
            )

        conversation = await self._resolve_conversation(
            payload.conversation_id, user_id, db
        )
        db.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content=content,
            )
        )
        await db.commit()
        return conversation.id

    async def stream(
        self, conversation_id: UUID, ctx: ToolContext, db: AsyncSession
    ) -> AsyncIterator[str]:
        """Streams SSE events: meta -> token* -> done | error, running tools in between."""
        yield _sse({"type": "meta", "conversation_id": str(conversation_id)})

        messages = await self._history(conversation_id, db)
        system_prompt = render_system_prompt(await build_user_context(ctx.user_id, db))

        # Untitled conversation: name it while the reply streams, costs no extra wait.
        title_task = (
            asyncio.create_task(self._generate_title(messages))
            if await self._missing_title(conversation_id, db)
            else None
        )

        reply: list[str] = []
        tools_run: list[dict] = []
        usage: StreamChunk | None = None
        try:
            for _ in range(MAX_TOOL_TURNS):
                calls: list[ToolCall] = []
                async for chunk in self.platform.chat(
                    messages, system_prompt=system_prompt, tools=TOOLS
                ):
                    if chunk.tool_calls:
                        calls = chunk.tool_calls
                    elif chunk.text:
                        reply.append(chunk.text)
                        yield _sse({"type": "token", "text": chunk.text})
                    if chunk.done:
                        usage = chunk
                if not calls:
                    break
                results = [await execute_tool(call, ctx, db) for call in calls]
                tools_run += _tool_log(calls, results)
                messages += _tool_turn(calls, results)

            if title_task is not None:
                await self._save_title(conversation_id, title_task, messages, db)
        except Exception:
            logger.exception("Chat stream failed for conversation %s", conversation_id)
            yield _sse(
                {"type": "error", "message": "Something went wrong. Please try again."}
            )
            return
        finally:
            if title_task is not None and not title_task.done():
                title_task.cancel()

        db.add(
            ChatMessage(
                conversation_id=conversation_id,
                role="model",
                content="".join(reply),
                metadata_={
                    "model": getattr(self.platform, "model", None),
                    "input_token": usage.input_token if usage else None,
                    "output_token": usage.output_token if usage else None,
                    "tools": tools_run or None,
                },
            )
        )
        await db.commit()
        yield _sse(
            {
                "type": "done",
                "conversation_id": str(conversation_id),
                "input_token": usage.input_token if usage else None,
                "output_token": usage.output_token if usage else None,
            }
        )

    async def _missing_title(self, conversation_id: UUID, db: AsyncSession) -> bool:
        title = (
            await db.execute(
                select(Conversation.title).where(Conversation.id == conversation_id)
            )
        ).scalar_one_or_none()
        return not title

    async def _generate_title(self, messages: list[Message]) -> str | None:
        """Ask the model for a short name for the conversation's opening message."""
        opening = next((m.content for m in messages if m.role == "user"), "")[:500]
        if not opening:
            return None
        parts: list[str] = []
        try:
            async for chunk in self.platform.chat(
                [Message(role="user", content=opening)], system_prompt=TITLE_PROMPT
            ):
                if chunk.text:
                    parts.append(chunk.text)
        except Exception:
            logger.exception("Conversation title generation failed")
            return None
        return _clean_title("".join(parts))

    async def _save_title(
        self,
        conversation_id: UUID,
        task: asyncio.Task,
        messages: list[Message],
        db: AsyncSession,
    ) -> None:
        """Persist the generated title alongside the reply, falling back to the opening text."""
        conversation = (
            await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalar_one_or_none()
        if conversation is None or conversation.title:
            return
        conversation.title = (await task) or _fallback_title(messages)

    async def list_conversations(
        self, user_id: str, db: AsyncSession
    ) -> list[ConversationResponse]:
        conversations = (
            (
                await db.execute(
                    select(Conversation)
                    .where(Conversation.user_id == user_id)
                    .order_by(Conversation.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        last_messages = await self._last_messages(conversations, db)
        responses = [ConversationResponse.model_validate(c) for c in conversations]
        for response in responses:
            response.last_message = last_messages.get(response.id)
        return responses

    async def get_conversation(
        self, conversation_id: UUID, user_id: str, db: AsyncSession
    ) -> ConversationDetailResponse:
        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=CHAT_MESSAGES.NOT_FOUND,
            )

        detail = ConversationDetailResponse.model_validate(conversation)
        detail.messages = [
            ChatMessageResponse.model_validate(m) for m in conversation.messages
        ]
        detail.last_message = detail.messages[-1] if detail.messages else None
        return detail

    async def delete_conversation(
        self, conversation_id: UUID, user_id: str, db: AsyncSession
    ) -> DeleteConversationResponse:
        """Hard-deletes the conversation and, through the FK cascade, every message
        in it, including everything ever replayed to the model."""
        conversation = await self._resolve_conversation(conversation_id, user_id, db)
        await db.delete(conversation)
        await db.commit()
        return DeleteConversationResponse(
            id=conversation_id, message=CHAT_MESSAGES.DELETED
        )

    async def _resolve_conversation(
        self, conversation_id: UUID | None, user_id: str, db: AsyncSession
    ) -> Conversation:
        if not conversation_id:
            conversation = Conversation(user_id=user_id)
            db.add(conversation)
            await db.commit()
            return conversation

        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=CHAT_MESSAGES.NOT_FOUND,
            )
        return conversation

    @staticmethod
    async def _last_messages(
        conversations: list[Conversation], db: AsyncSession
    ) -> dict[UUID, ChatMessageResponse]:
        """Latest message per conversation, one DISTINCT ON query (Postgres)."""
        if not conversations:
            return {}
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id.in_([c.id for c in conversations]))
            .distinct(ChatMessage.conversation_id)
            .order_by(ChatMessage.conversation_id, ChatMessage.created_at.desc())
        )
        return {
            m.conversation_id: ChatMessageResponse.model_validate(m)
            for m in result.scalars().all()
        }

    @staticmethod
    async def _history(conversation_id: UUID, db: AsyncSession) -> list[Message]:
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
        return [
            Message(role=m.role, content=m.content)
            for m in reversed(result.scalars().all())
        ]
