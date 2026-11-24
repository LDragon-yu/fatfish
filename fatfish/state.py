from __future__ import annotations

import math
import time
from datetime import datetime, tzinfo
from typing import Any

from .db import Database

EMOTIONS: dict[str, tuple[str, float, float]] = {
    "joy": ("高兴", 55, 120),
    "anger": ("生气", 3, 45),
    "sad": ("委屈", 5, 120),
    "shy": ("害羞", 3, 15),
    "lonely": ("孤独", 10, 180),
    "bored": ("无聊", 20, 60),
    "jealous": ("吃醋", 0, 60),
    "proud": ("得意", 10, 60),
    "worry": ("担心", 5, 240),
    "annoyed": ("烦躁", 8, 60),
    "arousal": ("兴致", 4, 30),
}
ENERGY_BASE = 70.0
ENERGY_HALF_LIFE = 240.0
ENERGY_NIGHT_BASE = 30.0

RELATIONSHIPS: dict[str, tuple[str, float]] = {
    "intimacy": ("亲密", 30),
    "trust": ("信任", 50),
    "familiar": ("熟悉", 35),
    "interest": ("兴趣", 80),
    "reliance": ("依赖", 15),
    "grudge": ("不满", 0),
}

DEFAULT_BEHAVIOR = {
    "user_messages": 0,
    "assistant_turns": 0,
    "proactive_turns": 0,
    "active_hours": [0] * 24,
    "last_user_ts": 0.0,
    "last_assistant_ts": 0.0,
    "last_assistant_proactive": False,
    "user_reply_latency_sum": 0.0,
    "user_reply_latency_count": 0,
    "assistant_reply_latency_sum": 0.0,
    "assistant_reply_latency_count": 0,
    "proactive_sent": 0,
    "proactive_replied": 0,
}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def default_mood() -> dict[str, float]:
    mood = {key: base for key, (_, base, _) in EMOTIONS.items()}
    mood["energy"] = ENERGY_BASE
    return mood


def default_relationship() -> dict[str, float]:
    return {key: base for key, (_, base) in RELATIONSHIPS.items()}


def overall(rel: dict[str, float]) -> float:
    return clamp(
        rel.get("intimacy", 0) * 0.35
        + rel.get("trust", 0) * 0.25
        + rel.get("interest", 0) * 0.15
        + rel.get("familiar", 0) * 0.10
        + rel.get("reliance", 0) * 0.15
        - rel.get("grudge", 0) * 0.5
    )


def level(score: float) -> str:
    for threshold, name in (
        (85, "非常亲近"),
        (70, "喜欢"),
        (55, "挺熟"),
        (40, "普通"),
        (25, "有点冷淡"),
        (0, "讨厌"),
    ):
        if score >= threshold:
            return name
    return "讨厌"


def _merge_defaults(raw: Any, defaults: dict[str, float]) -> dict[str, float]:
    out = dict(defaults)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in out:
                try:
                    out[key] = float(value)
                except (TypeError, ValueError):
                    pass
    return out


async def load_state(
    db: Database, user_id: int
) -> tuple[dict[str, float], dict[str, float]]:
    mood = _merge_defaults(
        await db.get_state(user_id, "mood", {}), default_mood()
    )
    rel = _merge_defaults(
        await db.get_state(user_id, "relationship", {}), default_relationship()
    )
    return mood, rel


async def save_state(
    db: Database,
    user_id: int,
    mood: dict[str, float],
    rel: dict[str, float],
    *,
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    await db.set_state(user_id, "mood", mood)
    await db.set_state(user_id, "relationship", rel)
    await db.set_state(user_id, "last_tick", float(now))


async def ensure_ready(
    db: Database, user_id: int, now: float | None = None
) -> None:
    now = time.time() if now is None else now
    if await db.get_state(user_id, "mood") is None:
        await db.set_state(user_id, "mood", default_mood())
    if await db.get_state(user_id, "relationship") is None:
        await db.set_state(user_id, "relationship", default_relationship())
    if await db.get_state(user_id, "last_tick") is None:
        await db.set_state(user_id, "last_tick", float(now))
    if await db.get_stat(user_id, "behavior") is None:
        await db.set_stat(user_id, "behavior", DEFAULT_BEHAVIOR)


def apply_decay(value: float, baseline: float, minutes: float, half_life: float) -> float:
    if minutes <= 0 or half_life <= 0:
        return value
    factor = 1.0 - 0.5 ** (minutes / half_life)
    return value + (baseline - value) * factor


async def tick(
    db: Database, user_id: int, now: float, tz: tzinfo
) -> tuple[dict[str, float], dict[str, float]]:
    """Apply half-life decay since the last tick."""
    await ensure_ready(db, user_id, now)
    mood, rel = await load_state(db, user_id)
    last_tick = await db.get_state(user_id, "last_tick", now)
    try:
        last_tick = float(last_tick)
    except (TypeError, ValueError):
        last_tick = now
    minutes = max(0.0, (now - last_tick) / 60.0)

    if minutes > 0.0:
        for key, (_, baseline, half_life) in EMOTIONS.items():
            mood[key] = clamp(apply_decay(mood[key], baseline, minutes, half_life))

        hour = datetime.fromtimestamp(now, tz).hour
        energy_base = ENERGY_NIGHT_BASE if 0 <= hour < 7 else ENERGY_BASE
        mood["energy"] = clamp(
            apply_decay(mood["energy"], energy_base, minutes, ENERGY_HALF_LIFE)
        )

        rel["grudge"] = clamp(apply_decay(rel["grudge"], 0.0, minutes, 1440.0))
        for key in RELATIONSHIPS:
            if key != "grudge":
                rel[key] = clamp(rel[key])

    await save_state(db, user_id, mood, rel, now=now)
    return mood, rel


async def apply_deltas(
    db: Database,
    user_id: int,
    emotion_deltas: dict[str, Any] | None = None,
    relationship_deltas: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    now = time.time() if now is None else now
    mood, rel = await load_state(db, user_id)
    for key, delta in (emotion_deltas or {}).items():
        if key in mood:
            try:
                mood[key] = clamp(mood[key] + float(delta))
            except (TypeError, ValueError):
                pass
    for key, delta in (relationship_deltas or {}).items():
        if key in rel:
            try:
                rel[key] = clamp(rel[key] + float(delta))
            except (TypeError, ValueError):
                pass
    await save_state(db, user_id, mood, rel, now=now)
    return mood, rel


def render_mood(mood: dict[str, float]) -> str:
    parts = [f"{label} {mood.get(key, 0):.0f}" for key, (label, _, _) in EMOTIONS.items()]
    parts.append(f"精力 {mood.get('energy', 0):.0f}")
    return "、".join(parts)


def render_relationship(rel: dict[str, float]) -> str:
    parts = [f"{label} {rel.get(key, 0):.0f}" for key, (label, _) in RELATIONSHIPS.items()]
    score = overall(rel)
    return "、".join(parts) + f"；综合好感 {score:.0f}（{level(score)}）"


def _human_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def render_last_interaction(stats: dict[str, Any], now: float) -> str:
    last_user = float(stats.get("last_user_ts") or 0)
    last_assistant = float(stats.get("last_assistant_ts") or 0)
    user_part = "还没有聊过" if last_user <= 0 else _human_age(now - last_user)
    assistant_part = "还没有主动说过话" if last_assistant <= 0 else _human_age(now - last_assistant)
    return f"他上次发消息：{user_part}；你上次说话：{assistant_part}"


def active_hours_summary(hours: list[int] | None) -> str:
    if not hours or len(hours) != 24 or not any(hours):
        return "暂无足够数据"
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for hour in range(25):
        active = hour < 24 and hours[hour] > 0
        if active and start is None:
            start = hour
        elif not active and start is not None:
            ranges.append((start, hour))
            start = None
    return "、".join(f"{a:02d}:00–{b:02d}:00" for a, b in ranges)


async def get_behavior(db: Database, user_id: int) -> dict[str, Any]:
    raw = await db.get_stat(user_id, "behavior", None)
    out = dict(DEFAULT_BEHAVIOR)
    if isinstance(raw, dict):
        out.update(raw)
    if not isinstance(out.get("active_hours"), list) or len(out["active_hours"]) != 24:
        out["active_hours"] = [0] * 24
    return out


async def record_user_message(
    db: Database, user_id: int, now: float, tz: tzinfo
) -> None:
    behavior = await get_behavior(db, user_id)
    last_user = float(behavior.get("last_user_ts") or 0)
    last_assistant = float(behavior.get("last_assistant_ts") or 0)

    if last_assistant > last_user:
        behavior["user_reply_latency_sum"] = float(
            behavior.get("user_reply_latency_sum") or 0
        ) + max(0.0, now - last_assistant)
        behavior["user_reply_latency_count"] = int(
            behavior.get("user_reply_latency_count") or 0
        ) + 1
        if behavior.get("last_assistant_proactive"):
            behavior["proactive_replied"] = int(behavior.get("proactive_replied") or 0) + 1

    behavior["user_messages"] = int(behavior.get("user_messages") or 0) + 1
    behavior["last_user_ts"] = now
    hour = datetime.fromtimestamp(now, tz).hour
    behavior["active_hours"][hour] = int(behavior["active_hours"][hour]) + 1
    await db.set_stat(user_id, "behavior", behavior)


async def record_assistant_turn(
    db: Database,
    user_id: int,
    now: float,
    *,
    proactive: bool,
    outgoing_count: int,
) -> None:
    if outgoing_count <= 0:
        return
    behavior = await get_behavior(db, user_id)
    last_user = float(behavior.get("last_user_ts") or 0)
    last_assistant = float(behavior.get("last_assistant_ts") or 0)

    if last_user > last_assistant:
        behavior["assistant_reply_latency_sum"] = float(
            behavior.get("assistant_reply_latency_sum") or 0
        ) + max(0.0, now - last_user)
        behavior["assistant_reply_latency_count"] = int(
            behavior.get("assistant_reply_latency_count") or 0
        ) + 1

    behavior["assistant_turns"] = int(behavior.get("assistant_turns") or 0) + 1
    if proactive:
        behavior["proactive_turns"] = int(behavior.get("proactive_turns") or 0) + 1
        behavior["proactive_sent"] = int(behavior.get("proactive_sent") or 0) + 1
    behavior["last_assistant_ts"] = now
    behavior["last_assistant_proactive"] = proactive
    await db.set_stat(user_id, "behavior", behavior)


async def profile_summary(db: Database, user_id: int, now: float) -> str:
    tags = await db.list_tags(user_id)
    behavior = await get_behavior(db, user_id)

    lines: list[str] = []
    if tags:
        tag_parts = []
        for tag in tags[:8]:
            confidence = float(tag.get("confidence") or 0)
            evidence = str(tag.get("evidence") or "").strip()
            extra = f"，{evidence}" if evidence else ""
            tag_parts.append(f"{tag['tag']}：{tag['value']}（{confidence:.1f}{extra}）")
        lines.append("- 标签：" + "；".join(tag_parts))
    else:
        lines.append("- 标签：还没有足够数据")

    user_latency_count = int(behavior.get("user_reply_latency_count") or 0)
    user_latency = (
        float(behavior.get("user_reply_latency_sum") or 0) / user_latency_count
        if user_latency_count
        else None
    )
    assistant_latency_count = int(behavior.get("assistant_reply_latency_count") or 0)
    assistant_latency = (
        float(behavior.get("assistant_reply_latency_sum") or 0) / assistant_latency_count
        if assistant_latency_count
        else None
    )
    proactive_sent = int(behavior.get("proactive_sent") or 0)
    proactive_replied = int(behavior.get("proactive_replied") or 0)
    proactive_rate = proactive_replied / proactive_sent if proactive_sent else None

    stats_parts = []
    if user_latency is not None:
        stats_parts.append(f"他平均 {user_latency:.0f} 秒回你")
    if assistant_latency is not None:
        stats_parts.append(f"你平均 {assistant_latency:.0f} 秒回他")
    if proactive_rate is not None:
        stats_parts.append(f"主动消息回复率 {proactive_rate:.0%}")
    stats_parts.append(f"活跃时段 {active_hours_summary(behavior.get('active_hours'))}")
    lines.append("- 统计：" + "；".join(stats_parts))

    return "\n".join(lines)
