import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_service.integrations.queries import interval


class EventProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(max_length=60)
    end: str = Field(max_length=60)
    description: str = Field(default="", max_length=5000)
    location: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def valid_range(self):
        interval(self.start, self.end)
        return self


class EmailProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    to: list[str] = Field(min_length=1, max_length=5)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=16000)

    @field_validator("to")
    @classmethod
    def recipients(cls, value):
        pattern = (
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}"
        )
        if any(len(v) > 254 or not re.fullmatch(pattern, v) for v in value):
            raise ValueError("invalid recipient")
        return list(dict.fromkeys(value))

    @field_validator("subject")
    @classmethod
    def header(cls, value):
        if "\r" in value or "\n" in value:
            raise ValueError("invalid subject")
        return value
