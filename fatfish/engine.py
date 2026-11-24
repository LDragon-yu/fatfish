from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import prompts, state, users
from .config import Settings
from .context import ContextBuilder
from .db import Database
from .llm import LLMClient
from .media import describe_image as describe_image_file
from .media import describe_video as describe_video_files
from .models import TurnResult
from .tools import TOOLS, ToolExecutor
from .tts import TTSService

logger = logging.getLogger(__name__)


class EventBus:
    """Fan-out outgoing turns to connected WebChat clients."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[TurnResult]] = set()

    def subscribe(self) -> asyncio.Queue[TurnResult]:
        queue: asyncio.Queue[TurnResult] = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TurnResult]) -> None:
        self._queues.discard(queue)

    def publish(self, result: TurnResult) -> None:
        for queue in list(self._queues):
            queue.put_nowait(result)


class Engine:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        llm: LLMClient,
        context: ContextBuilder,
        tts: TTSService | None = None,
    ):
        self.db = db
        self.settings = settings
        self.llm = llm
        self.vision_llm = llm
        self.context = context
        self.tts = tts
        self.tts_for: (
            Callable[[int], Awaitable[TTSService | None]] | None
        ) = None
        self.bus = EventBus()
        self.lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def is_busy(self) -> bool:
        return self.lock.locked()

    async def handle_user_message(
        self,
        user_id: int,
        text: str,
        *,
        channel: str = "text",
        source_id: str | None = None,
        media: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        text = (text or "").strip()
        if not text:
            return TurnResult(trigger="user", user_id=int(user_id))
        if source_id and await self.db.message_exists(user_id, source_id):
            return TurnResult(trigger="user", duplicate=True, user_id=int(user_id))

        async with self.lock:
            now = time.time()
            if source_id and await self.db.message_exists(user_id, source_id):
                return TurnResult(trigger="user", duplicate=True, user_id=int(user_id))
            await state.tick(self.db, user_id, now, self.settings.tz)
            meta = {"media": media} if media else None
            await self.db.add_message(
                user_id,
                "user",
                text,
                channel=channel,
                source_id=source_id,
                ts=now,
                meta=meta,
            )
            await state.record_user_message(self.db, user_id, now, self.settings.tz)
            messages = await self.context.build(
                user_id, "user", user_text=text, media=media
            )
            result = await self._run_turn(
                user_id, "user", messages, now=now, summary=text[:200]
            )
            return result

    async def handle_proactive(
        self, user_id: int, thought: dict[str, Any] | None = None
    ) -> TurnResult:
        async with self.lock:
            now = time.time()
            await state.tick(self.db, user_id, now, self.settings.tz)
            messages = await self.context.build(user_id, "proactive", thought=thought)
            summary = ""
            if thought:
                summary = str(thought.get("topic") or thought.get("reason") or "主动思考")
            result = await self._run_turn(
                user_id,
                "proactive",
                messages,
                now=now,
                summary=summary[:200],
                thought=thought,
            )
            return result

    async def describe_image(self, path: Path) -> str:
        """One-sentence description appended after the [图片] placeholder."""
        if not self.settings.media_describe:
            return ""
        try:
            return await describe_image_file(self.vision_llm, path)
        except Exception as exc:  # noqa: BLE001 - description is best effort
            logger.warning("图片描述生成失败：%s", exc)
            return ""

    async def describe_video(
        self,
        frames: list[Path],
        transcript: str = "",
        duration: float | None = None,
    ) -> str:
        """One-sentence description appended after the [视频] placeholder."""
        if not self.settings.media_describe:
            return ""
        try:
            return await describe_video_files(
                self.vision_llm, frames, transcript, duration
            )
        except Exception as exc:  # noqa: BLE001 - description is best effort
            logger.warning("视频描述生成失败：%s", exc)
            return ""

    async def precheck_thought(
        self, user_id: int, thought: dict[str, Any]
    ) -> dict[str, Any]:
        """在真正触发待办前，检查话题是否已经被最近的聊天覆盖。

        返回 action: fire / cancel / revise。
        预检失败时默认 fire，避免误吞主动消息。
        """
        created = float(thought.get("created_at") or 0)
        messages = await self.db.messages_since(user_id, created, 40)
        lines: list[str] = []
        for row in messages:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            who = "你" if row.get("role") == "assistant" else "他"
            lines.append(f"{who}：{text[:180]}")
        if not lines:
            return {"action": "fire", "reason": "安排之后没有新聊天"}
        if len(lines) > 30:
            lines = lines[-30:]

        scheduled = (
            datetime.fromtimestamp(created, self.settings.tz).strftime("%m-%d %H:%M")
            if created
            else "未知"
        )
        reason = str(thought.get("reason") or "").strip() or "主动想想"
        topic = str(thought.get("topic") or "").strip()
        prompt = f"""你是主动消息预检器。下面是原本安排在 {scheduled} 的主动消息，以及从安排之后到现在的聊天记录。
判断这条待办现在是否还值得发。

规则：
- 如果聊天已经覆盖了待办的核心内容（例如已经聊过组会、已经知道对方吃了），不要再提；能保留的只有仍然自然、未被覆盖的部分。
- 如果现在明显不合适（对方在忙、已经道晚安、话题已经结束），可以取消。
- 不确定就保留原样。

只输出 JSON，不要解释：
{{"action":"fire|cancel|revise","reason":"一句话原因","new_reason":"改写后的待办原因（revise 时必填）","new_topic":"改写后的话题（可空）"}}

待办原因：{reason}
待办话题：{topic or "无"}
聊天记录：
{chr(10).join(lines)}"""
        try:
            response = await self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "你是主动消息预检器，只输出 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as exc:  # noqa: BLE001 - 预检失败不阻塞主动消息
            logger.warning("主动思考预检失败，按原计划触发：%s", exc)
            return {"action": "fire", "reason": f"预检失败：{exc}"}

        content = str(response.content or "").strip()
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            logger.warning("主动思考预检返回无法解析，按原计划触发：%s", content[:200])
            return {"action": "fire", "reason": "预检返回无法解析"}
        try:
            payload = json.loads(content[start : end + 1])
        except (TypeError, ValueError):
            logger.warning("主动思考预检 JSON 解析失败，按原计划触发")
            return {"action": "fire", "reason": "预检 JSON 解析失败"}

        action = str(payload.get("action") or "").strip().lower()
        if action not in {"fire", "cancel", "revise"}:
            action = "fire"
        result = {
            "action": action,
            "reason": str(payload.get("reason") or "")[:200],
            "new_reason": str(payload.get("new_reason") or "").strip(),
            "new_topic": str(payload.get("new_topic") or "").strip(),
        }
        if action == "revise" and not result["new_reason"]:
            result["action"] = "fire"
            result["reason"] = "预检建议改写但未给新原因"
        logger.info(
            "主动思考预检 user=%s action=%s reason=%s",
            user_id,
            result["action"],
            result["reason"],
        )
        return result

    async def _run_turn(
        self,
        user_id: int,
        trigger: str,
        messages: list[dict[str, Any]],
        *,
        now: float,
        summary: str,
        thought: dict[str, Any] | None = None,
    ) -> TurnResult:
        executor = ToolExecutor(self.db, self.settings, user_id)
        executor.reset(now)
        all_calls = []
        error: str | None = None

        for _round in range(max(1, self.settings.max_tool_rounds)):
            try:
                response = await self.llm.chat(messages, tools=TOOLS)
            except Exception as exc:  # noqa: BLE001 - fall back to a short text
                error = f"{type(exc).__name__}: {exc}"
                self.last_error = error
                logger.exception("模型调用失败，发送兜底文本")
                user = await users.get_user(self.db, user_id)
                bot_name = users.bot_name_for(user, self.settings)
                await executor.execute(
                    "send_text",
                    {
                        "text": prompts.fallback_error_text(
                            self.settings,
                            user_id=user_id,
                            bot_name=bot_name,
                        ),
                        "delay": 0,
                    },
                    now=now,
                )
                break

            if response.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [call.to_openai() for call in response.tool_calls],
                    }
                )
                for call in response.tool_calls:
                    all_calls.append(call)
                    result = await executor.execute(
                        call.name, call.arguments, now=now
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                names = {call.name for call in response.tool_calls}
                if "no_op" in names:
                    break
                if any(name.startswith("send_") for name in names):
                    # The model already expressed its outgoing messages.
                    # Sending is terminal for this turn to avoid duplicate sends.
                    break
                # Bookkeeping only (remember/topic/tag/state): let the model
                # take one more round in case it wants to speak after updating.
                continue

            if response.content and not executor.outgoing:
                await executor.execute(
                    "send_text", {"text": response.content, "delay": 0}, now=now
                )
            break

        if self.tts_for is not None:
            tts = await self.tts_for(user_id)
        else:
            tts = self.tts
        if tts is not None:
            for item in executor.outgoing:
                if item.kind != "voice":
                    continue
                try:
                    audio = await tts.synthesize(
                        item.text, voice=item.voice or "siliconflow"
                    )
                    item.meta["audio_path"] = str(audio)
                except Exception as exc:  # noqa: BLE001 - fall back to text
                    item.meta["tts_error"] = f"{type(exc).__name__}: {exc}"
                    logger.warning("TTS 合成失败，将回退文字：%s", exc)
                message_id = item.meta.get("message_id")
                if message_id:
                    await self.db.update_message_meta(int(message_id), item.meta)

        await state.record_assistant_turn(
            self.db,
            user_id,
            now,
            proactive=(trigger == "proactive"),
            outgoing_count=len(executor.outgoing),
        )

        decision = {
            "trigger": trigger,
            "thought": thought,
            "calls": [
                {"name": call.name, "arguments": call.arguments} for call in all_calls
            ],
            "outgoing": [
                {
                    "kind": item.kind,
                    "text": item.text[:200],
                    "delay": item.delay,
                    "voice": item.voice,
                    "audio": bool(item.meta.get("audio_path")),
                    "tts_error": item.meta.get("tts_error"),
                }
                for item in executor.outgoing
            ],
            "error": error,
        }
        await self.db.log_decision(
            user_id,
            trigger,
            summary,
            decision,
            model="mock" if self.settings.mock_llm else self.settings.deepseek_model,
        )

        result = TurnResult(
            outgoing=executor.outgoing,
            tool_calls=all_calls,
            trigger=trigger,
            error=error,
            user_id=int(user_id),
        )
        if result.outgoing:
            self.bus.publish(result)
        return result
