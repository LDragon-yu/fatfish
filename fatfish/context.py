from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import prompts, state, users
from .config import Settings
from .db import Database
from .media import image_part

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _relative(ts: float | None, now: float) -> str:
    if not ts:
        return "未知"
    seconds = max(0.0, now - float(ts))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def _until(ts: float | None, now: float) -> str:
    if not ts:
        return "未定"
    seconds = float(ts) - now
    if seconds <= 0:
        return "已到期"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟后"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时后"
    return f"{int(seconds // 86400)} 天后"


def _format_ts(ts: float | None, settings: Settings) -> str:
    if not ts:
        return "未定"
    return datetime.fromtimestamp(float(ts), settings.tz).strftime("%m-%d %H:%M")


def _text_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(str(part.get("text") or ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return 0


class ContextBuilder:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    async def build(
        self,
        user_id: int,
        trigger: str,
        *,
        user_text: str | None = None,
        thought: dict[str, Any] | None = None,
        media: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        mood, rel = await state.load_state(self.db, user_id)
        dynamic = await self._dynamic_blocks(
            user_id, trigger, user_text, thought, now, mood, rel
        )
        user = await users.get_user(self.db, user_id)
        bot_name = users.bot_name_for(user, self.settings)
        system = (
            prompts.stable_system(
                self.settings, user_id=user_id, bot_name=bot_name
            )
            + "\n\n"
            + dynamic
        )
        history = await self._history(user_id, trigger, user_text)
        if media and trigger == "user":
            self._apply_media(history, media)

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        if trigger == "proactive":
            now_text = self._now_text(now)
            messages.append(
                {
                    "role": "user",
                    "content": prompts.proactive_cue(
                        self.settings, now_text, thought, user_id=user_id
                    ),
                }
            )

        total = sum(_text_len(m.get("content")) for m in messages)
        while total > self.settings.max_context_chars and len(history) > 6:
            removed = history.pop(0)
            total -= _text_len(removed.get("content"))
        return messages

    def _apply_media(
        self, history: list[dict[str, Any]], media: list[dict[str, Any]]
    ) -> None:
        index = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                index = i
                break
        if index is None:
            return
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": str(history[index].get("content") or "")}
        ]
        for item in media:
            kind = item.get("kind")
            if kind == "image":
                part = image_part(self.settings.data_dir, item.get("path"))
                if part is not None:
                    parts.append(part)
            elif kind == "video":
                frames = item.get("frames") or []
                transcript = str(item.get("transcript") or "").strip()
                duration = item.get("duration")
                note = "（他发来一段视频"
                if duration:
                    note += f"，约 {int(float(duration))} 秒"
                note += f"，附 {len(frames)} 张关键帧截图"
                note += f"；音轨转写：{transcript}）" if transcript else "；没有识别到语音）"
                parts.append({"type": "text", "text": note})
                for frame in frames:
                    part = image_part(self.settings.data_dir, frame)
                    if part is not None:
                        parts.append(part)
        history[index]["content"] = parts

    def _now_text(self, now: float) -> str:
        dt = datetime.fromtimestamp(now, self.settings.tz)
        return f"{dt.strftime('%Y-%m-%d %H:%M')} {WEEKDAYS[dt.weekday()]}（{self.settings.timezone}）"

    async def _history(
        self, user_id: int, trigger: str, user_text: str | None
    ) -> list[dict[str, Any]]:
        rows = await self.db.recent_messages(user_id, self.settings.recent_messages)
        history: list[dict[str, Any]] = []
        for row in rows:
            role = row.get("role")
            text = str(row.get("text") or "").strip()
            if role not in {"user", "assistant"} or not text:
                continue
            if history and history[-1]["role"] == role:
                history[-1]["content"] += "\n" + text
            else:
                history.append({"role": role, "content": text})

        if trigger == "user":
            if user_text:
                if not history or history[-1]["role"] != "user":
                    history.append({"role": "user", "content": user_text})
                elif user_text not in history[-1]["content"]:
                    history[-1]["content"] += "\n" + user_text
            if history and history[0]["role"] == "assistant":
                history.insert(0, {"role": "user", "content": "（接着之前的聊天）"})
        return history

    async def _dynamic_blocks(
        self,
        user_id: int,
        trigger: str,
        user_text: str | None,
        thought: dict[str, Any] | None,
        now: float,
        mood: dict[str, float],
        rel: dict[str, float],
    ) -> str:
        behavior = await state.get_behavior(self.db, user_id)
        profile = await state.profile_summary(self.db, user_id, now)
        topics = await self.db.list_topics(user_id, ("open", "waiting"))
        thoughts = await self.db.list_pending_thoughts(user_id, 10)

        query = (user_text or "").strip()
        if not query:
            recent = await self.db.recent_messages(user_id, 4)
            for row in reversed(recent):
                if row.get("role") == "user" and str(row.get("text") or "").strip():
                    query = str(row["text"]).strip()
                    break
        if not query and thought:
            query = str(thought.get("topic") or thought.get("reason") or "").strip()
        if not query and topics:
            query = str(topics[0].get("name") or "").strip()

        facts = await self.db.search_facts(user_id, query, 8) if query else []
        raw = await self.db.search_messages(user_id, query, 6) if query else []

        blocks: list[str] = []
        blocks.append("[关系]\n" + state.render_relationship(rel))
        blocks.append("[你的状态]\n" + state.render_mood(mood))
        blocks.append("[关于他]\n" + profile)

        if topics:
            lines = []
            for topic in topics:
                check = (
                    f"，下次可看 {_until(topic.get('next_check_at'), now)}"
                    if topic.get("next_check_at")
                    else ""
                )
                lines.append(
                    f"- {topic['name']}（{topic['status']}，重要度 {topic.get('importance', 3)}"
                    f"，上次提到 {_relative(topic.get('last_mentioned'), now)}{check}）"
                )
            blocks.append("[未完成话题]\n" + "\n".join(lines))

        if thoughts:
            lines = []
            for item in thoughts:
                topic = f"（话题：{item['topic']}）" if item.get("topic") else ""
                lines.append(
                    f"- {_until(item.get('due_at'), now)}到期：{_clip(item.get('reason') or '', 80)}{topic}"
                )
            blocks.append("[待触发]\n" + "\n".join(lines))

        memory_lines: list[str] = []
        if facts:
            memory_lines.extend(
                f"- {fact.get('kind', 'fact')}：{_clip(fact.get('content') or '', 220)}"
                for fact in facts
            )
        if raw:
            for row in raw:
                if user_text and str(row.get("text") or "").strip() == user_text.strip():
                    continue
                who = "我" if row.get("role") == "assistant" else "他"
                memory_lines.append(
                    f"- 原话（{_format_ts(row.get('ts'), self.settings)}，{who}）："
                    f"{_clip(row.get('text') or '', 160)}"
                )
        if memory_lines:
            blocks.append("[长期记忆]\n" + "\n".join(memory_lines[:12]))
        elif query:
            blocks.append("[长期记忆]\n（没有检索到相关记忆，不要编）")

        last_interaction = state.render_last_interaction(behavior, now)
        active = state.active_hours_summary(behavior.get("active_hours"))
        now_lines = [
            self._now_text(now),
            last_interaction,
            f"他的活跃时段（推断）：{active}",
        ]
        if trigger == "proactive":
            now_lines.append("这次不是他发消息，而是你自己醒来，想看看要不要主动找他。")
            if thought:
                if thought.get("topic"):
                    now_lines.append(f"触发话题：{thought['topic']}")
                if thought.get("reason"):
                    now_lines.append(f"触发原因：{thought['reason']}")
        else:
            now_lines.append("他刚发来一条消息。")
        blocks.append("[现在]\n" + "\n".join(now_lines))

        return "\n\n".join(blocks)
