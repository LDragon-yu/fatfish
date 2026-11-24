from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from . import state
from .config import Settings
from .db import Database
from .models import Outgoing


def _fn(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _fn(
        "send_text",
        "发一条文字消息。所有要发出去的文字都用这个工具，不要用普通回复重复。",
        {
            "text": {"type": "string", "description": "要发送的文字"},
            "delay": {"type": "number", "description": "发送前等待秒数，默认 0"},
        },
        ["text"],
    ),
    _fn(
        "send_voice",
        "发一条语音。默认 siliconflow=你的克隆音色；也可以 moli=MiMo 备用音色，clone 等同克隆音色。长内容也可以发语音。",
        {
            "text": {"type": "string", "description": "要念出来的文字"},
            "delay": {"type": "number", "description": "发送前等待秒数，默认 0"},
            "voice": {
                "type": "string",
                "enum": ["siliconflow", "moli", "clone"],
                "description": "siliconflow=你的克隆音色（默认），moli=MiMo 备用音色，clone=克隆音色",
            },
        },
        ["text"],
    ),
    _fn(
        "send_burst",
        "一次发多条消息，按顺序执行，每条有自己的延迟。适合连续发几条语音或文字。",
        {
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text", "voice"]},
                        "text": {"type": "string"},
                        "delay": {"type": "number"},
                        "voice": {
                            "type": "string",
                            "enum": ["siliconflow", "moli", "clone"],
                        },
                    },
                    "required": ["type", "text"],
                },
            }
        },
        ["messages"],
    ),
    _fn(
        "schedule_think",
        "安排下一次主动思考。到时间后系统会叫醒你，你再决定要不要发消息。",
        {
            "in_minutes": {"type": "number", "description": "多少分钟后再想，5–720"},
            "reason": {"type": "string", "description": "为什么想起这件事"},
        },
        ["in_minutes", "reason"],
    ),
    _fn(
        "open_topic",
        "开一个未完成话题，并可安排到期检查。比如面试、看展、等通知。",
        {
            "name": {"type": "string", "description": "话题名"},
            "importance": {"type": "integer", "description": "1–5，默认 3"},
            "next_check_minutes": {
                "type": "number",
                "description": "可选：多少分钟后再看这个话题",
            },
        },
        ["name"],
    ),
    _fn(
        "update_topic",
        "更新话题状态（open/waiting/closed），可重新安排检查。",
        {
            "name": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "waiting", "closed"]},
            "next_check_minutes": {"type": "number"},
        },
        ["name", "status"],
    ),
    _fn(
        "close_topic",
        "结束一个话题，之后不再主动提；用户重新提起时可以再打开。",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _fn(
        "update_tag",
        "记录或修正你观察到的用户习惯/偏好。要带证据和置信度，不要从一句话过度推断。",
        {
            "tag": {"type": "string"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "description": "0–1"},
            "evidence": {"type": "string"},
        },
        ["tag", "value"],
    ),
    _fn(
        "remember",
        "存一条长期记忆：事实、偏好、事件或对某人的印象。",
        {
            "kind": {
                "type": "string",
                "enum": ["fact", "preference", "event", "impression"],
            },
            "content": {"type": "string"},
            "topic": {"type": "string"},
            "importance": {"type": "integer", "description": "1–5，默认 3"},
        },
        ["kind", "content"],
    ),
    _fn(
        "forget",
        "删除/停用相关记忆和话题。用户说别记了、忘了这件事时用。",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _fn(
        "update_state",
        "小幅更新自己的情绪和关系数值。普通聊天 0–±1，明显事件 ±2，极端才 ±5。",
        {
            "emotion_deltas": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": "如 {\"joy\": 1, \"lonely\": -1, \"energy\": -2}",
            },
            "relationship_deltas": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": "如 {\"intimacy\": 1, \"trust\": 1}",
            },
        },
        [],
    ),
    _fn(
        "no_op",
        "现在不想说话/不适合发消息。可以安排下次再想。",
        {
            "reason": {"type": "string"},
            "next_think_minutes": {"type": "number", "description": "可选：下次再想"},
        },
        ["reason"],
    ),
]


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# 入站语音会显示成 [语音：转写]，模型偶尔会照抄成普通文本。发送前兜底改回语音。
_VOICE_MARKER = re.compile(r"\s*\[语音\s*[:：]\s*(.*?)\s*\]\s*", re.S)


class ToolExecutor:
    """Execute model tool calls and collect outgoing messages."""

    def __init__(self, db: Database, settings: Settings, user_id: int):
        self.db = db
        self.settings = settings
        self.user_id = int(user_id)
        self.outgoing: list[Outgoing] = []
        self.calls: list[dict[str, Any]] = []
        self._cursor_ts = time.time()

    def reset(self, now: float | None = None) -> None:
        self.outgoing = []
        self.calls = []
        self._cursor_ts = time.time() if now is None else float(now)

    async def execute(self, name: str, args: Any, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        self.calls.append({"name": name, "arguments": args})

        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        try:
            return await handler(args, now)
        except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---------------- sending ----------------

    async def _send(
        self,
        kind: str,
        text: str,
        delay: float,
        voice: str | None,
        now: float,
    ) -> dict[str, Any]:
        text = text.strip()
        if not text:
            return {"ok": False, "error": "消息内容为空"}
        if kind == "text":
            match = _VOICE_MARKER.fullmatch(text)
            if match and match.group(1).strip():
                kind = "voice"
                text = match.group(1).strip()
                voice = voice or "siliconflow"
        if (
            kind == "text"
            and self.settings.voice_probability > 0
            and len(text) <= self.settings.tts_max_chars
            and random.random() < self.settings.voice_probability
        ):
            kind = "voice"
            voice = voice or "siliconflow"
        delay = _clamp(_as_float(delay, 0.0), 0.0, 600.0)
        ts = self._cursor_ts + delay
        self._cursor_ts = ts
        meta = {"delay": delay, "tool": f"send_{kind}"}
        if kind == "voice":
            meta["voice"] = voice or "siliconflow"
        message_id = await self.db.add_message(
            self.user_id, "assistant", text, channel=kind, ts=ts, meta=meta
        )
        meta["message_id"] = message_id
        await self.db.update_message_meta(message_id, meta)
        self.outgoing.append(
            Outgoing(
                kind="voice" if kind == "voice" else "text",
                text=text,
                delay=delay,
                voice=(voice or "siliconflow") if kind == "voice" else None,
                meta=meta,
            )
        )
        return {"ok": True, "kind": kind, "delay": delay}

    async def _tool_send_text(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        return await self._send(
            "text",
            _as_str(args.get("text")),
            _as_float(args.get("delay"), 0.0),
            None,
            now,
        )

    async def _tool_send_voice(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        voice = _as_str(args.get("voice"), "siliconflow")
        if voice not in {"siliconflow", "moli", "clone"}:
            voice = "siliconflow"
        return await self._send(
            "voice",
            _as_str(args.get("text")),
            _as_float(args.get("delay"), 0.0),
            voice,
            now,
        )

    async def _tool_send_burst(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        items = args.get("messages")
        if not isinstance(items, list):
            return {"ok": False, "error": "messages 必须是数组"}
        if len(items) > self.settings.max_burst_messages:
            items = items[: self.settings.max_burst_messages]
        sent: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                errors.append("非对象消息")
                continue
            kind = _as_str(item.get("type"), "text")
            if kind not in {"text", "voice"}:
                errors.append(f"不支持的 type：{kind}")
                continue
            voice = _as_str(item.get("voice"), "siliconflow")
            if voice not in {"siliconflow", "moli", "clone"}:
                voice = "siliconflow"
            result = await self._send(
                kind,
                _as_str(item.get("text")),
                _as_float(item.get("delay"), 0.0),
                voice if kind == "voice" else None,
                now,
            )
            if result.get("ok"):
                sent.append(result)
            else:
                errors.append(str(result.get("error") or "发送失败"))
        return {"ok": bool(sent), "sent": len(sent), "errors": errors}

    # ---------------- time / topics ----------------

    async def _tool_schedule_think(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        minutes = _clamp(
            _as_float(args.get("in_minutes"), 60.0),
            float(self.settings.min_think_minutes),
            float(self.settings.max_think_minutes),
        )
        reason = _as_str(args.get("reason"), "主动想想") or "主动想想"
        due_at = now + minutes * 60.0
        thought_id = await self.db.schedule_thought(self.user_id, due_at, reason)
        return {"ok": True, "thought_id": thought_id, "due_at": due_at, "in_minutes": minutes}

    async def _tool_open_topic(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        name = _as_str(args.get("name"))
        if not name:
            return {"ok": False, "error": "话题名为空"}
        importance = int(_clamp(_as_float(args.get("importance"), 3), 1, 5))
        next_minutes = args.get("next_check_minutes")
        next_check_at = None
        if next_minutes is not None:
            minutes = _clamp(_as_float(next_minutes, 0.0), 1.0, 10080.0)
            next_check_at = now + minutes * 60.0
        await self.db.upsert_topic(
            self.user_id,
            name,
            status="open",
            importance=importance,
            last_mentioned=now,
            next_check_at=next_check_at,
        )
        if next_check_at is not None:
            await self.db.upsert_thought_for_topic(
                self.user_id, next_check_at, f"看看话题：{name}", name
            )
        return {"ok": True, "name": name, "next_check_at": next_check_at}

    async def _tool_update_topic(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        name = _as_str(args.get("name"))
        if not name:
            return {"ok": False, "error": "话题名为空"}
        status = _as_str(args.get("status"), "open")
        if status not in {"open", "waiting", "closed"}:
            return {"ok": False, "error": f"不支持的状态：{status}"}
        existing = await self.db.get_topic(self.user_id, name)
        importance = _as_int((existing or {}).get("importance"), 3)
        next_check_at = None if status == "closed" else (existing or {}).get("next_check_at")
        next_minutes = args.get("next_check_minutes")
        if status != "closed" and next_minutes is not None:
            minutes = _clamp(_as_float(next_minutes, 0.0), 1.0, 10080.0)
            next_check_at = now + minutes * 60.0
        await self.db.upsert_topic(
            self.user_id,
            name,
            status=status,
            importance=importance,
            last_mentioned=now,
            next_check_at=next_check_at,
            meta=json.loads((existing or {}).get("meta") or "{}"),
        )
        if status == "closed":
            await self.db.cancel_thoughts_for_topic(self.user_id, name)
        elif next_minutes is not None and next_check_at is not None:
            await self.db.upsert_thought_for_topic(
                self.user_id, next_check_at, f"看看话题：{name}", name
            )
        return {"ok": True, "name": name, "status": status, "next_check_at": next_check_at}

    async def _tool_close_topic(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        return await self._tool_update_topic(
            {"name": args.get("name"), "status": "closed"}, now
        )

    # ---------------- memory / profile ----------------

    async def _tool_update_tag(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        tag = _as_str(args.get("tag"))
        value = _as_str(args.get("value"))
        if not tag or not value:
            return {"ok": False, "error": "tag/value 不能为空"}
        confidence = _clamp(_as_float(args.get("confidence"), 0.5), 0.0, 1.0)
        evidence = _as_str(args.get("evidence"))
        await self.db.upsert_tag(
            self.user_id, tag, value, confidence=confidence, evidence=evidence
        )
        return {"ok": True, "tag": tag, "value": value, "confidence": confidence}

    async def _tool_remember(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        kind = _as_str(args.get("kind"), "fact")
        if kind not in {"fact", "preference", "event", "impression"}:
            kind = "fact"
        content = _as_str(args.get("content"))
        if not content:
            return {"ok": False, "error": "记忆内容为空"}
        topic = _as_str(args.get("topic"))
        importance = int(_clamp(_as_float(args.get("importance"), 3), 1, 5))
        fact_id = await self.db.add_fact(
            self.user_id, kind, content, topic=topic, importance=importance, ts=now
        )
        return {"ok": True, "fact_id": fact_id, "kind": kind, "topic": topic}

    async def _tool_forget(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        query = _as_str(args.get("query"))
        if not query:
            return {"ok": False, "error": "query 为空"}
        facts = await self.db.deactivate_facts(self.user_id, query)
        thoughts = await self.db.cancel_thoughts_matching(self.user_id, query)
        topics = await self.db.close_topics_matching(self.user_id, query)
        return {"ok": True, "facts": facts, "thoughts": thoughts, "topics": topics}

    # ---------------- state / no_op ----------------

    async def _tool_update_state(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        emotion_deltas = args.get("emotion_deltas")
        relationship_deltas = args.get("relationship_deltas")
        if not isinstance(emotion_deltas, dict):
            emotion_deltas = {}
        if not isinstance(relationship_deltas, dict):
            relationship_deltas = {}
        mood, rel = await state.apply_deltas(
            self.db,
            self.user_id,
            emotion_deltas,
            relationship_deltas,
            now=now,
        )
        return {
            "ok": True,
            "mood": {k: round(v, 1) for k, v in mood.items()},
            "relationship": {k: round(v, 1) for k, v in rel.items()},
        }

    async def _tool_no_op(self, args: dict[str, Any], now: float) -> dict[str, Any]:
        reason = _as_str(args.get("reason"), "现在不想说话")
        result: dict[str, Any] = {"ok": True, "reason": reason, "scheduled": False}
        next_minutes = args.get("next_think_minutes")
        if next_minutes is not None:
            minutes = _clamp(
                _as_float(next_minutes, 60.0),
                float(self.settings.min_think_minutes),
                float(self.settings.max_think_minutes),
            )
            due_at = now + minutes * 60.0
            thought_id = await self.db.schedule_thought(self.user_id, due_at, reason)
            result.update({"scheduled": True, "thought_id": thought_id, "due_at": due_at})
        return result
