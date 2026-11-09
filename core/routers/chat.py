from typing import Any, Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai.base import ToolContext
from core.schemas.chat import SendMessageRequest
from core.security import current_user, require_pro_user
from core.services.chat import ChatService
from db.database import get_db

chat_router = APIRouter(
    prefix="/chats",
    tags=["Chats"],
    dependencies=[Depends(require_pro_user)],
)

chat_service = ChatService()


def _tool_context(current_user: Mapping[str, Any]) -> ToolContext:
    return ToolContext(
        user_id=str(current_user["id"]),
        subscription_status=current_user["subscription_status"],
        timezone=current_user.get("timezone"),
    )


@chat_router.post(
    "/send",
    status_code=status.HTTP_200_OK,
    description=(
        "Sends a message and streams the assistant reply as server-sent events "
        "(meta -> token* -> done | error)."
    ),
)
async def send(
    payload: SendMessageRequest,
    current_user: Mapping[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation_id = await chat_service.prepare(
        payload=payload, user_id=str(current_user["id"]), db=db
    )
    return StreamingResponse(
        chat_service.stream(
            conversation_id=conversation_id,
            ctx=_tool_context(current_user),
            db=db,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@chat_router.get(
    "",
    status_code=status.HTTP_200_OK,
    description="Lists the user's conversations, newest first.",
)
async def list_conversations(
    current_user: Mapping[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.list_conversations(str(current_user["id"]), db)


@chat_router.get(
    "/{conversation_id}",
    status_code=status.HTTP_200_OK,
    description="One conversation with its messages, oldest first.",
)
async def get_conversation(
    conversation_id: UUID,
    current_user: Mapping[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.get_conversation(
        conversation_id, str(current_user["id"]), db
    )


@chat_router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_200_OK,
    description="Deletes a conversation and every message in it.",
)
async def delete_conversation(
    conversation_id: UUID,
    current_user: Mapping[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.delete_conversation(
        conversation_id, str(current_user["id"]), db
    )
