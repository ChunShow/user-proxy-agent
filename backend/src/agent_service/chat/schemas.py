from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Empty message")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    messages: list[Message] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def valid_history(self):
        if self.messages[0].role != "user" or self.messages[-1].role != "user":
            raise ValueError("User message required")
        if sum(len(m.content) for m in self.messages) > 60000:
            raise ValueError("Conversation too long")
        return self
