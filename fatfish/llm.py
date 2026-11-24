from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Protocol

from openai import AsyncOpenAI

from .config import Settings
from .models import LLMResponse, LLMToolCall

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


class OpenAILLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=90.0,
            max_retries=0,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                if not response.choices:
                    raise LLMError("模型没有返回 choices")
                message = response.choices[0].message
                tool_calls: list[LLMToolCall] = []
                for call in message.tool_calls or []:
                    raw_args = call.function.arguments or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError:
                        arguments = {"_raw": raw_args}
                    if not isinstance(arguments, dict):
                        arguments = {"_value": arguments}
                    tool_calls.append(
                        LLMToolCall(
                            id=call.id or f"call_{uuid.uuid4().hex[:8]}",
                            name=call.function.name,
                            arguments=arguments,
                        )
                    )
                content = (message.content or "").strip()
                if not content and not tool_calls:
                    raise LLMError("模型返回了空内容")
                return LLMResponse(content=content or None, tool_calls=tool_calls)
            except Exception as exc:  # noqa: BLE001 - retry provider errors
                last_error = exc
                logger.warning("DeepSeek 调用失败（第 %s 次）：%s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise LLMError(f"DeepSeek 调用失败：{last_error}")


def _call(tool_name: str, **arguments: Any) -> LLMToolCall:
    return LLMToolCall(
        id=f"mock_{uuid.uuid4().hex[:8]}",
        name=tool_name,
        arguments=arguments,
    )


class MockLLM:
    """Deterministic model for local development and tests.

    Accepts an optional script of LLMResponse objects; when the script runs out
    it falls back to simple heuristics.
    """

    def __init__(self, script: list[LLMResponse] | None = None):
        self.script = list(script or [])

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if self.script:
            return self.script.pop(0)

        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = str(message.get("content") or "")
                break

        if "系统触发" in last_user:
            return LLMResponse(
                content=None,
                tool_calls=[
                    _call("no_op", reason="mock：现在先不主动", next_think_minutes=60)
                ],
            )
        if "面试" in last_user or "考试" in last_user or "明天" in last_user:
            return LLMResponse(
                content=None,
                tool_calls=[
                    _call("send_text", text="明天几点呀？", delay=0),
                    _call("send_voice", text="别太紧张，你准备得挺认真的。", delay=1.2),
                    _call("open_topic", name="面试", importance=4, next_check_minutes=1080),
                    _call(
                        "remember",
                        kind="event",
                        content=f"他说：{last_user[:120]}",
                        topic="面试",
                        importance=4,
                    ),
                    _call(
                        "update_state",
                        emotion_deltas={"worry": 2},
                        relationship_deltas={"trust": 1},
                    ),
                ],
            )
        if "语音" in last_user or "声音" in last_user or "晚安" in last_user:
            return LLMResponse(
                content=None,
                tool_calls=[_call("send_voice", text="好呀，你听。", delay=0)],
            )
        if "记住" in last_user:
            return LLMResponse(
                content=None,
                tool_calls=[
                    _call("remember", kind="fact", content=last_user[:200], importance=3),
                    _call("send_text", text="记住啦。", delay=0),
                ],
            )
        if last_user.strip():
            return LLMResponse(
                content=None,
                tool_calls=[_call("send_text", text="嗯嗯，我在听。", delay=0)],
            )
        return LLMResponse(content=None, tool_calls=[_call("no_op", reason="mock：没有输入")])


def build_llm(settings: Settings) -> LLMClient:
    if settings.mock_llm or not settings.deepseek_api_key:
        logger.info("使用 mock 模型（没有 DEEPSEEK_API_KEY 或 FATFISH_MOCK_LLM=1）")
        return MockLLM()
    logger.info("使用 DeepSeek 模型：%s", settings.deepseek_model)
    return OpenAILLM(settings)
