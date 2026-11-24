from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class Outgoing:
    """A message the model wants to send."""

    kind: Literal["text", "voice"]
    text: str
    delay: float = 0.0
    voice: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(slots=True)
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)


@dataclass(slots=True)
class TurnResult:
    outgoing: list[Outgoing] = field(default_factory=list)
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    trigger: str = "user"
    error: str | None = None
    duplicate: bool = False
    user_id: int = 0
