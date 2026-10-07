from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChatMessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    metadata: dict | None = Field(default=None, validation_alias="metadata_")
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class ConversationResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message: ChatMessageResponse | None = None

    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    messages: list[ChatMessageResponse] = []


class SendMessageRequest(BaseModel):
    content: str
    conversation_id: UUID | None = None


class DeleteConversationResponse(BaseModel):
    id: UUID
    message: str
