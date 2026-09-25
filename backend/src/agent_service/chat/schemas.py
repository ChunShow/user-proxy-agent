from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    conversation_id: UUID
    content: str | None = Field(None, min_length=1, max_length=12000)
    retry_message_id: UUID | None = None

    @model_validator(mode="after")
    def valid_input(self):
        if (self.content is None) == (self.retry_message_id is None):
            raise ValueError("Exactly one input required")
        if self.content is not None and not self.content.strip():
            raise ValueError("Nonblank input required")
        return self
