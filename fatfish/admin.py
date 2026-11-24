from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import prompts, settings_store, state, users, voices
from .config import Settings
from .runtime import runtime
from .tts import build_tts

SETTING_GROUPS: list[dict[str, Any]] = [
    {
        "name": "模型与密钥",
        "fields": [
            {
                "key": "DEEPSEEK_API_KEY",
                "attr": "deepseek_api_key",
                "label": "DeepSeek API Key",
                "type": "secret",
                "help": "聊天、视觉描述、主动思考都用它。留空表示不修改。",
            },
            {
                "key": "DEEPSEEK_BASE_URL",
                "attr": "deepseek_base_url",
                "label": "DeepSeek Base URL",
                "type": "text",
            },
            {
                "key": "DEEPSEEK_MODEL",
                "attr": "deepseek_model",
                "label": "聊天模型",
                "type": "text",
                "suggestions": ["deepseek-flash", "deepseek-chat"],
            },
            {
                "key": "FATFISH_VISION_MODEL",
                "attr": "vision_model",
                "label": "视觉描述模型",
                "type": "text",
                "help": "留空或与聊天模型相同即可。",
                "suggestions": ["deepseek-flash"],
            },
            {
                "key": "SILICONFLOW_API_KEY",
                "attr": "siliconflow_api_key",
                "label": "SiliconFlow API Key",
                "type": "secret",
                "help": "TTS 与 ASR 共用。",
            },
            {
                "key": "SILICONFLOW_BASE_URL",
                "attr": "siliconflow_base_url",
                "label": "SiliconFlow Base URL",
                "type": "text",
            },
            {
                "key": "SILICONFLOW_ASR_MODEL",
                "attr": "siliconflow_asr_model",
                "label": "ASR 模型",
                "type": "text",
                "suggestions": ["FunAudioLLM/SenseVoiceSmall"],
            },
            {
                "key": "MIMO_API_KEY",
                "attr": "mimo_api_key",
                "label": "MiMo API Key",
                "type": "secret",
                "help": "备用 TTS（MiMo）。",
            },
            {
                "key": "MIMO_BASE_URL",
                "attr": "mimo_base_url",
                "label": "MiMo Base URL",
                "type": "text",
            },
            {
                "key": "MIMO_TTS_MODEL",
                "attr": "mimo_tts_model",
                "label": "MiMo TTS 模型",
                "type": "text",
                "suggestions": ["mimo-v2.5-tts"],
            },
            {
                "key": "BREEZE_API_KEY",
                "attr": "breeze_api_key",
                "label": "Breeze Blue API Key",
                "type": "secret",
                "help": "可选 TTS；在 Breeze Blue 控制台创建，用于克隆与合成。",
            },
            {
                "key": "BREEZE_BASE_URL",
                "attr": "breeze_base_url",
                "label": "Breeze Base URL",
                "type": "text",
            },
            {
                "key": "BREEZE_TTS_MODEL",
                "attr": "breeze_tts_model",
                "label": "Breeze TTS 模型",
                "type": "text",
                "suggestions": ["breeze-tts-2"],
            },
            {
                "key": "BREEZE_LANGUAGE_CODE",
                "attr": "breeze_language_code",
                "label": "Breeze 语音语言",
                "type": "text",
                "suggestions": ["zh", "en"],
            },
        ],
    },
    {
        "name": "行为参数",
        "fields": [
            {
                "key": "FATFISH_TEMPERATURE",
                "attr": "llm_temperature",
                "label": "温度",
                "type": "float",
                "min": 0,
                "max": 2,
                "step": 0.05,
            },
            {
                "key": "FATFISH_MAX_TOKENS",
                "attr": "llm_max_tokens",
                "label": "单次最大 tokens",
                "type": "int",
                "min": 100,
                "max": 8000,
            },
            {
                "key": "FATFISH_RECENT_MESSAGES",
                "attr": "recent_messages",
                "label": "上下文消息条数",
                "type": "int",
                "min": 4,
                "max": 200,
            },
            {
                "key": "FATFISH_MAX_CONTEXT_CHARS",
                "attr": "max_context_chars",
                "label": "上下文最大字符数",
                "type": "int",
                "min": 1000,
                "max": 500000,
            },
            {
                "key": "FATFISH_MAX_TOOL_ROUNDS",
                "attr": "max_tool_rounds",
                "label": "单轮最多工具轮数",
                "type": "int",
                "min": 1,
                "max": 20,
            },
            {
                "key": "FATFISH_MAX_BURST",
                "attr": "max_burst_messages",
                "label": "单轮最多消息数",
                "type": "int",
                "min": 1,
                "max": 100,
            },
            {
                "key": "FATFISH_SCHEDULER_INTERVAL",
                "attr": "scheduler_interval_seconds",
                "label": "主动思考检查间隔（秒）",
                "type": "int",
                "min": 5,
                "max": 3600,
            },
            {
                "key": "FATFISH_MIN_THINK_MINUTES",
                "attr": "min_think_minutes",
                "label": "下次思考最短（分钟）",
                "type": "int",
                "min": 1,
                "max": 1440,
            },
            {
                "key": "FATFISH_MAX_THINK_MINUTES",
                "attr": "max_think_minutes",
                "label": "下次思考最长（分钟）",
                "type": "int",
                "min": 5,
                "max": 10080,
            },
            {
                "key": "FATFISH_PROACTIVE",
                "attr": "proactive_enabled",
                "label": "开启主动消息",
                "type": "bool",
            },
            {
                "key": "TTS_MAX_CHARS",
                "attr": "tts_max_chars",
                "label": "单条语音最大字数",
                "type": "int",
                "min": 50,
                "max": 2000,
            },
            {
                "key": "FATFISH_VOICE_PROBABILITY",
                "attr": "voice_probability",
                "label": "文字回复转语音概率",
                "type": "float",
                "min": 0,
                "max": 1,
                "step": 0.05,
                "help": "模型本来发文字时，按此概率自动改发语音；0=不强制，1=全部转语音（超长文本除外）。",
            },
            {
                "key": "FATFISH_SPLIT_MESSAGES",
                "attr": "split_messages",
                "label": "回复按回车拆成多条",
                "type": "bool",
            },
            {
                "key": "FATFISH_SPLIT_DELAY",
                "attr": "split_delay_seconds",
                "label": "拆条最短间隔（秒）",
                "type": "float",
                "min": 0,
                "max": 2,
                "step": 0.05,
                "help": "实际间隔按下一段消息长度模拟打字速度（约 4 字/秒，0.6–8 秒）。",
            },
            {
                "key": "FATFISH_SPLIT_MAX_SEGMENTS",
                "attr": "split_max_segments",
                "label": "单条最多拆成几段",
                "type": "int",
                "min": 1,
                "max": 50,
            },
            {
                "key": "FATFISH_LOG_LEVEL",
                "attr": "log_level",
                "label": "日志级别",
                "type": "select",
                "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
            },
        ],
    },
    {
        "name": "媒体理解",
        "fields": [
            {
                "key": "FATFISH_MEDIA_ENABLED",
                "attr": "media_enabled",
                "label": "启用图片/视频理解",
                "type": "bool",
            },
            {
                "key": "FATFISH_MEDIA_DESCRIBE",
                "attr": "media_describe",
                "label": "自动生成媒体描述",
                "type": "bool",
                "help": "关闭后只保留 [图片]/[视频] 占位符。",
            },
            {
                "key": "FATFISH_IMAGE_MAX_SIDE",
                "attr": "image_max_side",
                "label": "图片最长边（px）",
                "type": "int",
                "min": 256,
                "max": 4096,
                "step": 64,
            },
            {
                "key": "FATFISH_IMAGE_MAX_MB",
                "attr": "image_max_mb",
                "label": "图片大小上限（MB）",
                "type": "int",
                "min": 1,
                "max": 30,
            },
            {
                "key": "FATFISH_VIDEO_FPS",
                "attr": "video_fps",
                "label": "视频抽帧帧率（帧/秒）",
                "type": "float",
                "min": 0.1,
                "max": 5,
                "step": 0.1,
            },
            {
                "key": "FATFISH_VIDEO_MAX_FRAMES",
                "attr": "video_max_frames",
                "label": "视频最多抽帧数",
                "type": "int",
                "min": 1,
                "max": 120,
            },
            {
                "key": "FATFISH_VIDEO_MAX_MB",
                "attr": "video_max_mb",
                "label": "视频大小上限（MB）",
                "type": "int",
                "min": 1,
                "max": 200,
            },
        ],
    },
    {
        "name": "QQ / OneBot",
        "fields": [
            {
                "key": "OWNER_QQ",
                "attr": "owner_qq",
                "label": "主人 QQ（OWNER_QQ）",
                "type": "text",
                "help": "只接受并回复这个 QQ 号的私聊。",
            },
            {
                "key": "ONEBOT_ACCESS_TOKEN",
                "attr": "onebot_access_token",
                "label": "OneBot Access Token",
                "type": "secret",
                "help": "需与 NapCat WebSocket 客户端里的 token 一致。",
            },
            {
                "key": "ONEBOT_ENABLED",
                "attr": "onebot_enabled",
                "label": "启用 OneBot",
                "type": "bool",
            },
        ],
    },
    {
        "name": "其他",
        "fields": [
            {"key": "FATFISH_BOT_NAME", "attr": "bot_name", "label": "机器人昵称", "type": "text"},
            {"key": "FATFISH_USER_NAME", "attr": "user_name", "label": "你的称呼", "type": "text"},
            {"key": "FATFISH_TZ", "attr": "timezone", "label": "时区", "type": "text"},
            {"key": "FFMPEG_PATH", "attr": "ffmpeg_path", "label": "ffmpeg 路径", "type": "text"},
            {
                "key": "FATFISH_HOST",
                "attr": "host",
                "label": "监听地址（重启生效）",
                "type": "text",
                "restart": True,
            },
            {
                "key": "FATFISH_PORT",
                "attr": "port",
                "label": "监听端口（重启生效）",
                "type": "int",
                "min": 1,
                "max": 65535,
                "restart": True,
            },
        ],
    },
]

FIELDS: list[dict[str, Any]] = [
    field for group in SETTING_GROUPS for field in group["fields"]
]
FIELD_BY_KEY = {field["key"]: field for field in FIELDS}


async def local_guard(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="管理接口仅限本机访问")
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("x-fatfish-admin") != "1":
        raise HTTPException(status_code=403, detail="缺少 X-Fatfish-Admin 头")
    origin = request.headers.get("origin")
    if origin:
        host_header = request.headers.get("host", "")
        allowed = {
            f"http://{host_header}",
            f"https://{host_header}",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        }
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="来源不被允许")


router = APIRouter(prefix="/api/admin", dependencies=[Depends(local_guard)])


def _field_payload(field: dict[str, Any], overlay: dict[str, str]) -> dict[str, Any]:
    value = getattr(runtime.settings, field["attr"])
    item = {
        "key": field["key"],
        "label": field["label"],
        "type": field["type"],
        "help": field.get("help", ""),
        "restart": bool(field.get("restart")),
        "from_overlay": field["key"] in overlay,
    }
    for key in ("min", "max", "step", "options", "suggestions"):
        if key in field:
            item[key] = field[key]
    if field["type"] == "secret":
        item["value"] = settings_store.mask_secret(str(value or ""))
        item["has_value"] = bool(str(value or ""))
    else:
        item["value"] = value
    return item


def _settings_payload() -> dict[str, Any]:
    overlay = settings_store.load_overlay()
    return {
        "groups": [
            {
                "name": group["name"],
                "fields": [_field_payload(field, overlay) for field in group["fields"]],
            }
            for group in SETTING_GROUPS
        ]
    }


def _coerce(field: dict[str, Any], raw: Any) -> str:
    kind = field["type"]
    if kind == "bool":
        if isinstance(raw, bool):
            return "1" if raw else "0"
        return "1" if str(raw).strip().lower() in {"1", "true", "yes", "on"} else "0"
    if kind == "int":
        try:
            value = int(float(raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{field['label']} 需要整数") from exc
        if "min" in field and value < field["min"]:
            raise HTTPException(status_code=400, detail=f"{field['label']} 不能小于 {field['min']}")
        if "max" in field and value > field["max"]:
            raise HTTPException(status_code=400, detail=f"{field['label']} 不能大于 {field['max']}")
        return str(value)
    if kind == "float":
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{field['label']} 需要数字") from exc
        if "min" in field and value < field["min"]:
            raise HTTPException(status_code=400, detail=f"{field['label']} 不能小于 {field['min']}")
        if "max" in field and value > field["max"]:
            raise HTTPException(status_code=400, detail=f"{field['label']} 不能大于 {field['max']}")
        return str(value)
    if kind == "select":
        value = str(raw)
        if value not in field.get("options", []):
            raise HTTPException(status_code=400, detail=f"{field['label']} 取值不合法")
        return value
    return str(raw or "").strip()


@router.get("/overview")
async def overview() -> dict[str, Any]:
    settings = runtime.settings
    db = runtime.db
    now = time.time()
    midnight = (
        datetime.fromtimestamp(now, settings.tz)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    today_row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM messages WHERE ts >= ?", (midnight,)
    )
    media_bytes = _dir_size(settings.data_dir / "media")
    try:
        db_bytes = settings.db_path.stat().st_size
    except OSError:
        db_bytes = 0
    active_voice = runtime.active_voice
    owner = await users.owner(db)
    user_rows = await db.fetchall(
        "SELECT status, COUNT(*) AS n FROM users GROUP BY status"
    )
    user_counts = {row["status"]: int(row["n"]) for row in user_rows}
    return {
        "settings": {
            "bot_name": settings.bot_name,
            "user_name": settings.user_name,
            "model": settings.deepseek_model,
            "vision_model": settings.vision_model,
            "mock": settings.mock_llm,
            "proactive": settings.proactive_enabled,
        },
        "users": {
            "total": sum(user_counts.values()),
            "active": user_counts.get("active", 0),
            "pending": user_counts.get("pending", 0),
            "blocked": user_counts.get("blocked", 0),
        },
        "services": {
            "tts": runtime.tts is not None,
            "tts_voice": (
                {
                    "id": active_voice.get("id"),
                    "name": active_voice.get("name"),
                    "provider": active_voice.get("provider"),
                    "mode": active_voice.get("mode"),
                }
                if active_voice
                else None
            ),
            "asr": runtime.asr is not None,
            "onebot": runtime.onebot.status(),
        },
        "db": {
            "path": str(settings.db_path),
            "messages": await db.count_all_messages(),
            "today": int(today_row["n"]) if today_row else 0,
            "bytes": db_bytes,
        },
        "media": {"bytes": media_bytes},
        "scheduler": {
            "proactive": settings.proactive_enabled,
            "pending_thoughts": await db.count_all_pending_thoughts(),
        },
        "prompts": {
            "customized": [
                name
                for name in prompts.PROMPT_NAMES
                if prompts.is_customized(
                    settings, name, int(owner["id"]) if owner else None
                )
            ]
        },
        "last_error": runtime.engine.last_error,
        "logs": _tail_log(settings, 40),
    }


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return _settings_payload()


@router.get("/users")
async def list_users() -> dict[str, Any]:
    return {"items": await users.list_users(runtime.db)}


@router.post("/users")
async def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    platform_id = str(payload.get("platform_id") or "").strip()
    if not platform_id.isdigit():
        raise HTTPException(status_code=400, detail="QQ 号必须是纯数字")
    status = str(payload.get("status") or "active")
    if status not in {"pending", "active", "blocked"}:
        raise HTTPException(status_code=400, detail="status 不合法")
    display_name = str(payload.get("display_name") or "").strip()
    user = await users.create_user(
        runtime.db,
        platform_id,
        display_name=display_name,
        status=status,
        role="user",
    )
    await runtime.ensure_user(int(user["id"]))
    return {"ok": True, "user": user}


@router.post("/users/{user_id}/status")
async def set_user_status(
    user_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    status = str(payload.get("status") or "").strip()
    try:
        user = await users.set_status(runtime.db, user_id, status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="用户不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if status == "active":
        await runtime.ensure_user(int(user_id))
    return {"ok": True, "user": user}


@router.put("/users/{user_id}/profile")
async def update_user_profile(
    user_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    await _user_or_404(user_id)
    fields: dict[str, str] = {}
    if "bot_name" in payload:
        fields["bot_name"] = str(payload.get("bot_name") or "")
    if "display_name" in payload:
        fields["display_name"] = str(payload.get("display_name") or "")
    user = await users.update_profile(runtime.db, user_id, **fields)
    return {"ok": True, "user": user}


async def _user_or_404(user_id: int) -> dict[str, Any]:
    user = await users.get_user(runtime.db, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _admin_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "text": row.get("text") or "",
        "channel": row.get("channel") or "text",
        "ts": row.get("ts") or 0,
    }


@router.get("/users/{user_id}/memory")
async def get_memory(user_id: int) -> dict[str, Any]:
    user = await _user_or_404(user_id)
    db = runtime.db
    facts = await db.list_facts(user_id, 200, include_inactive=True)
    tags = await db.list_tags(user_id)
    topics = await db.list_topics(user_id)
    thoughts = await db.list_thoughts(user_id, 200)
    mood, rel = await state.load_state(db, user_id)
    behavior = await state.get_behavior(db, user_id)
    messages = await db.recent_messages(user_id, 30)
    return {
        "user": user,
        "facts": facts,
        "tags": tags,
        "topics": topics,
        "thoughts": thoughts,
        "mood": mood,
        "relationship": rel,
        "behavior": behavior,
        "messages": [_admin_message(row) for row in messages],
    }


@router.post("/users/{user_id}/facts")
async def create_fact(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await _user_or_404(user_id)
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="事实内容不能为空")
    kind = str(payload.get("kind") or "fact")
    if kind not in {"fact", "preference", "event", "impression"}:
        kind = "fact"
    importance = max(1, min(5, int(payload.get("importance") or 3)))
    fact_id = await runtime.db.add_fact(
        user_id,
        kind,
        content,
        topic=str(payload.get("topic") or ""),
        importance=importance,
    )
    fact = await runtime.db.fetchone(
        "SELECT * FROM facts WHERE id=?", (fact_id,)
    )
    return {"ok": True, "fact": fact}


@router.put("/users/{user_id}/facts/{fact_id}")
async def update_fact(
    user_id: int, fact_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    await _user_or_404(user_id)
    fields: dict[str, Any] = {}
    for key in ("kind", "topic", "content"):
        if key in payload:
            fields[key] = str(payload.get(key) or "")
    if "importance" in payload:
        fields["importance"] = max(
            1, min(5, int(payload.get("importance") or 3))
        )
    if "active" in payload:
        fields["active"] = bool(payload.get("active"))
    fact = await runtime.db.update_fact(user_id, fact_id, **fields)
    if fact is None:
        raise HTTPException(status_code=404, detail="事实不存在")
    return {"ok": True, "fact": fact}


@router.delete("/users/{user_id}/facts/{fact_id}")
async def delete_fact(user_id: int, fact_id: int) -> dict[str, Any]:
    await _user_or_404(user_id)
    deleted = await runtime.db.delete_fact(user_id, fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="事实不存在")
    return {"ok": True}


@router.put("/users/{user_id}/tags")
async def upsert_tag(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await _user_or_404(user_id)
    tag = str(payload.get("tag") or "").strip()
    value = str(payload.get("value") or "").strip()
    if not tag or not value:
        raise HTTPException(status_code=400, detail="tag/value 不能为空")
    confidence = max(
        0.0, min(1.0, float(payload.get("confidence") or 0.5))
    )
    await runtime.db.upsert_tag(
        user_id,
        tag,
        value,
        confidence=confidence,
        evidence=str(payload.get("evidence") or ""),
        source="admin",
    )
    row = await runtime.db.fetchone(
        "SELECT * FROM tags WHERE user_id=? AND tag=?", (user_id, tag)
    )
    return {"ok": True, "tag": row}


@router.delete("/users/{user_id}/tags")
async def delete_tag(user_id: int, tag: str) -> dict[str, Any]:
    await _user_or_404(user_id)
    deleted = await runtime.db.delete_tag(user_id, tag)
    if not deleted:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"ok": True}


@router.put("/users/{user_id}/topics")
async def upsert_topic(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await _user_or_404(user_id)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="话题名不能为空")
    status = str(payload.get("status") or "open")
    if status not in {"open", "waiting", "closed"}:
        raise HTTPException(status_code=400, detail="话题状态不合法")
    importance = max(1, min(5, int(payload.get("importance") or 3)))
    next_check_at = payload.get("next_check_at")
    if next_check_at is not None:
        try:
            next_check_at = float(next_check_at)
        except (TypeError, ValueError):
            next_check_at = None
    await runtime.db.upsert_topic(
        user_id,
        name,
        status=status,
        importance=importance,
        last_mentioned=time.time(),
        next_check_at=next_check_at,
    )
    topic = await runtime.db.get_topic(user_id, name)
    return {"ok": True, "topic": topic}


@router.delete("/users/{user_id}/topics")
async def delete_topic(user_id: int, name: str) -> dict[str, Any]:
    await _user_or_404(user_id)
    deleted = await runtime.db.delete_topic(user_id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="话题不存在")
    return {"ok": True}


@router.post("/users/{user_id}/thoughts")
async def create_thought(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await _user_or_404(user_id)
    minutes = max(1.0, float(payload.get("in_minutes") or 60))
    thought_id = await runtime.db.schedule_thought(
        user_id,
        time.time() + minutes * 60.0,
        str(payload.get("reason") or "主动想想"),
        topic=str(payload.get("topic") or ""),
    )
    thought = await runtime.db.fetchone(
        "SELECT * FROM scheduled_thoughts WHERE id=?", (thought_id,)
    )
    return {"ok": True, "thought": thought}


@router.put("/users/{user_id}/thoughts/{thought_id}")
async def update_thought(
    user_id: int, thought_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    await _user_or_404(user_id)
    fields: dict[str, Any] = {}
    if "in_minutes" in payload:
        minutes = max(1.0, float(payload.get("in_minutes") or 60))
        fields["due_at"] = time.time() + minutes * 60.0
    if "reason" in payload:
        fields["reason"] = str(payload.get("reason") or "")
    if "topic" in payload:
        fields["topic"] = str(payload.get("topic") or "")
    if "status" in payload:
        status = str(payload.get("status") or "")
        if status not in {"pending", "cancelled", "done"}:
            raise HTTPException(status_code=400, detail="状态不合法")
        fields["status"] = status
    thought = await runtime.db.update_thought(user_id, thought_id, **fields)
    if thought is None:
        raise HTTPException(status_code=404, detail="待触发不存在")
    return {"ok": True, "thought": thought}


@router.delete("/users/{user_id}/thoughts/{thought_id}")
async def delete_thought(user_id: int, thought_id: int) -> dict[str, Any]:
    await _user_or_404(user_id)
    deleted = await runtime.db.delete_thought(user_id, thought_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="待触发不存在")
    return {"ok": True}


@router.put("/users/{user_id}/state")
async def update_state(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await _user_or_404(user_id)
    mood, rel = await state.load_state(runtime.db, user_id)
    raw_mood = payload.get("mood")
    if isinstance(raw_mood, dict):
        for key, value in raw_mood.items():
            if key in mood:
                try:
                    mood[key] = state.clamp(float(value))
                except (TypeError, ValueError):
                    pass
    raw_rel = payload.get("relationship")
    if isinstance(raw_rel, dict):
        for key, value in raw_rel.items():
            if key in rel:
                try:
                    rel[key] = state.clamp(float(value))
                except (TypeError, ValueError):
                    pass
    await state.save_state(runtime.db, user_id, mood, rel)
    return {"ok": True, "mood": mood, "relationship": rel}


@router.post("/users/{user_id}/state/reset")
async def reset_state(user_id: int) -> dict[str, Any]:
    await _user_or_404(user_id)
    mood = state.default_mood()
    rel = state.default_relationship()
    await state.save_state(runtime.db, user_id, mood, rel)
    return {"ok": True, "mood": mood, "relationship": rel}


@router.get("/users/{user_id}/messages")
async def get_messages(
    user_id: int, query: str = "", limit: int = 50
) -> dict[str, Any]:
    await _user_or_404(user_id)
    limit = max(1, min(300, int(limit)))
    if query.strip():
        rows = await runtime.db.search_messages(user_id, query, limit)
    else:
        rows = await runtime.db.recent_messages(user_id, limit)
    return {"items": [_admin_message(row) for row in rows]}


@router.delete("/users/{user_id}/messages/{message_id}")
async def delete_message(user_id: int, message_id: int) -> dict[str, Any]:
    await _user_or_404(user_id)
    deleted = await runtime.db.delete_message(user_id, message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}


@router.put("/settings")
async def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="values 必须是对象")
    updates: dict[str, str] = {}
    for key, raw in values.items():
        field = FIELD_BY_KEY.get(str(key))
        if field is None:
            continue
        if field["type"] == "secret":
            text = "" if raw is None else str(raw)
            if not text or text.startswith("…") or text == "••••":
                continue
            updates[str(key)] = text.strip()
            continue
        updates[str(key)] = _coerce(field, raw)
    if updates:
        settings_store.save_overrides(updates)
        await runtime.apply_settings(Settings.load())
    return _settings_payload()


# ---------------- prompts ----------------


async def _prompt_user(user_id: int) -> dict[str, Any]:
    return await _user_or_404(user_id)


@router.get("/prompts")
async def list_prompts(user_id: int = 1) -> dict[str, Any]:
    settings = runtime.settings
    user = await _prompt_user(user_id)
    items = []
    for name in prompts.PROMPT_NAMES:
        items.append(
            {
                "name": name,
                "label": prompts.PROMPT_LABELS[name],
                "placeholders": prompts.PROMPT_PLACEHOLDERS.get(name, []),
                "customized": prompts.is_customized(settings, name, user_id),
                "text": prompts.read_prompt(settings, name, user_id),
                "default": prompts.default_prompt(name),
            }
        )
    return {
        "items": items,
        "user": user,
        "bot_name": users.bot_name_for(user, settings),
    }


@router.get("/system-prompt")
async def system_prompt(user_id: int = 1) -> dict[str, str]:
    user = await _prompt_user(user_id)
    return {
        "text": prompts.stable_system(
            runtime.settings,
            user_id=user_id,
            bot_name=users.bot_name_for(user, runtime.settings),
        )
    }


@router.put("/prompts/{name}")
async def save_prompt(
    name: str, payload: dict[str, Any], user_id: int = 1
) -> dict[str, Any]:
    await _prompt_user(user_id)
    if name not in prompts.PROMPT_NAMES:
        raise HTTPException(status_code=404, detail="提示词不存在")
    text = str(payload.get("text") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="提示词不能为空，使用恢复默认")
    prompts.save_prompt(runtime.settings, name, text, user_id)
    return {"ok": True}


@router.post("/prompts/{name}/reset")
async def reset_prompt(name: str, user_id: int = 1) -> dict[str, Any]:
    await _prompt_user(user_id)
    if name not in prompts.PROMPT_NAMES:
        raise HTTPException(status_code=404, detail="提示词不存在")
    prompts.reset_prompt(runtime.settings, name, user_id)
    return {"ok": True}


@router.get("/prompts/{name}/history")
async def prompt_history(name: str, user_id: int = 1) -> dict[str, Any]:
    await _prompt_user(user_id)
    if name not in prompts.PROMPT_NAMES:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {
        "items": prompts.list_history(runtime.settings, name, user_id)
    }


@router.post("/prompts/{name}/restore")
async def restore_prompt(
    name: str, payload: dict[str, Any], user_id: int = 1
) -> dict[str, Any]:
    await _prompt_user(user_id)
    if name not in prompts.PROMPT_NAMES:
        raise HTTPException(status_code=404, detail="提示词不存在")
    filename = str(payload.get("file") or "")
    if not filename:
        raise HTTPException(status_code=400, detail="缺少 file")
    try:
        prompts.restore_history(runtime.settings, name, filename, user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="历史版本不存在") from exc
    return {"ok": True}


# ---------------- voices ----------------


def _profile_public(profile: dict[str, Any]) -> dict[str, Any]:
    data = dict(profile)
    reference = str(data.get("reference_path") or "")
    data["has_reference"] = bool(reference and Path(reference).is_file())
    data["reference_file"] = Path(reference).name if reference else ""
    return data


@router.get("/voices")
async def list_voices(user_id: int = 1) -> dict[str, Any]:
    await _user_or_404(user_id)
    service = await runtime.tts_for(user_id)
    profiles = await voices.list_profiles(runtime.db, user_id)
    active = await voices.get_active(runtime.db, user_id)
    return {
        "items": [_profile_public(profile) for profile in profiles],
        "active_id": active.get("id") if active else None,
        "tts_available": service is not None,
        "defaults": {
            "siliconflow_model": runtime.settings.siliconflow_tts_model,
            "mimo_voice": runtime.settings.mimo_tts_voice,
        },
    }


@router.post("/voices")
async def create_voice(
    name: str = Form(...),
    provider: str = Form("siliconflow"),
    mode: str = Form("references"),
    model: str = Form(""),
    speed: float = Form(0.8),
    voice_name: str = Form(""),
    transcript: str = Form(""),
    user_id: int = Form(1),
    audio: UploadFile | None = File(None),
) -> dict[str, Any]:
    await _user_or_404(user_id)
    provider = provider.strip() or "siliconflow"
    if provider not in {"siliconflow", "mimo", "breeze"}:
        raise HTTPException(
            status_code=400,
            detail="provider 只能是 siliconflow、mimo 或 breeze",
        )
    if provider == "mimo":
        mode = "preset"
        voice_name = (voice_name or runtime.settings.mimo_tts_voice).strip()
        profile = await voices.create_profile(
            runtime.db,
            user_id,
            name=name or f"MiMo 预设（{voice_name}）",
            provider="mimo",
            mode="preset",
            model=model or runtime.settings.mimo_tts_model,
            voice_name=voice_name,
            active=False,
        )
        await runtime.reload_voice(user_id)
        return {"ok": True, "profile": _profile_public(profile)}

    if provider == "breeze":
        if audio is None:
            raise HTTPException(status_code=400, detail="Breeze 需要上传参考音频")
        suffix = Path(audio.filename or "").suffix.lower()
        if suffix not in {
            ".mp3", ".wav", ".m4a", ".flac", ".aac",
            ".ogg", ".opus", ".webm", ".wma", ".aiff", ".aif",
        }:
            raise HTTPException(status_code=400, detail="Breeze 不支持该音频格式")
        data = await audio.read()
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Breeze 参考音频不能超过 5 MB")
        try:
            voice_id = await voices.clone_breeze_voice(
                runtime.settings,
                data,
                audio.filename or f"sample{suffix}",
                name or "Breeze 克隆音色",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        stored = voices.store_reference(runtime.settings, data, suffix)
        profile = await voices.create_profile(
            runtime.db,
            user_id,
            name=name or "Breeze 克隆音色",
            provider="breeze",
            mode="voice_id",
            model=runtime.settings.breeze_tts_model,
            reference_path=str(stored),
            speed=1.0,
            voice_uri=voice_id,
            active=False,
        )
        await runtime.reload_voice(user_id)
        return {"ok": True, "profile": _profile_public(profile)}

    if mode not in {"references", "upload"}:
        raise HTTPException(status_code=400, detail="siliconflow 只支持 references/upload")
    if audio is None:
        raise HTTPException(status_code=400, detail="需要上传参考音频")
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
        raise HTTPException(status_code=400, detail="音频格式只支持 mp3/wav/m4a/flac/ogg")
    data = await audio.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="参考音频不能超过 30 MB")
    if mode == "references" and not transcript.strip():
        raise HTTPException(status_code=400, detail="references 模式需要参考音频逐字稿")
    stored = voices.store_reference(runtime.settings, data, suffix)
    profile = await voices.create_profile(
        runtime.db,
        user_id,
        name=name or "克隆音色",
        provider="siliconflow",
        mode=mode,
        model=model or runtime.settings.siliconflow_tts_model,
        reference_path=str(stored),
        reference_text=transcript.strip(),
        speed=speed,
        active=False,
    )
    await runtime.reload_voice(user_id)
    return {"ok": True, "profile": _profile_public(profile)}


async def _profile_or_404(
    profile_id: int, user_id: int | None = None
) -> dict[str, Any]:
    profile = await voices.get_profile(runtime.db, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    if user_id is not None and int(profile["user_id"]) != int(user_id):
        raise HTTPException(status_code=404, detail="音色不存在")
    return profile


@router.post("/voices/{profile_id}/activate")
async def activate_voice(
    profile_id: int, user_id: int | None = None
) -> dict[str, Any]:
    profile = await _profile_or_404(profile_id, user_id)
    try:
        profile = await voices.set_active(runtime.db, profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="音色不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await runtime.reload_voice(int(profile["user_id"]))
    return {"ok": True, "profile": _profile_public(profile)}


@router.post("/voices/{profile_id}/archive")
async def archive_voice(
    profile_id: int, user_id: int | None = None
) -> dict[str, Any]:
    profile = await _profile_or_404(profile_id, user_id)
    try:
        profile = await voices.archive_profile(runtime.db, profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="音色不存在") from exc
    await runtime.reload_voice(int(profile["user_id"]))
    return {"ok": True, "profile": _profile_public(profile)}


@router.delete("/voices/{profile_id}")
async def delete_voice(
    profile_id: int, user_id: int | None = None
) -> dict[str, Any]:
    profile = await _profile_or_404(profile_id, user_id)
    try:
        profile = await voices.delete_profile(runtime.db, profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="音色不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await runtime.reload_voice(int(profile["user_id"]))
    return {"ok": True, "profile": _profile_public(profile)}


@router.post("/voices/{profile_id}/preview")
async def preview_voice(
    profile_id: int, payload: dict[str, Any], user_id: int | None = None
) -> dict[str, Any]:
    profile = await _profile_or_404(profile_id, user_id)
    text = str(payload.get("text") or "").strip() or "你好呀，这是音色预览。"
    service = build_tts(runtime.settings, profile)
    if service is None:
        raise HTTPException(status_code=400, detail="该音色缺少可用的 API key")
    try:
        path = await service.synthesize(text)
    except Exception as exc:  # noqa: BLE001 - report provider errors
        raise HTTPException(status_code=502, detail=f"合成失败：{exc}") from exc
    return {"ok": True, "audio_url": f"/api/audio/{path.name}"}


@router.post("/voices/{profile_id}/upload-provider")
async def upload_voice(
    profile_id: int, user_id: int | None = None
) -> dict[str, Any]:
    profile = await _profile_or_404(profile_id, user_id)
    if profile.get("provider") != "siliconflow":
        raise HTTPException(status_code=400, detail="只有 SiliconFlow 音色需要上传")
    try:
        uri = await voices.upload_voice_to_provider(runtime.settings, profile)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    updated = await voices.update_profile(
        runtime.db, profile_id, mode="upload", voice_uri=uri
    )
    await runtime.reload_voice(int(updated["user_id"]))
    return {"ok": True, "profile": _profile_public(updated)}


@router.get("/voices/{profile_id}/reference")
async def voice_reference(
    profile_id: int, user_id: int | None = None
):
    profile = await _profile_or_404(profile_id, user_id)
    path = Path(str(profile.get("reference_path") or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="没有参考音频")
    return FileResponse(path)


# ---------------- tests & logs ----------------


@router.post("/test/llm")
async def test_llm() -> dict[str, Any]:
    if runtime.settings.mock_llm:
        return {"ok": False, "error": "当前是 mock 模型（未配置 DEEPSEEK_API_KEY）"}
    started = time.time()
    try:
        response = await runtime.llm.chat(
            [{"role": "user", "content": "只回复两个字：正常"}]
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": bool(response.content),
        "reply": response.content,
        "model": runtime.settings.deepseek_model,
        "latency_ms": int((time.time() - started) * 1000),
    }


@router.post("/test/asr")
async def test_asr(audio: UploadFile = File(...)) -> dict[str, Any]:
    if runtime.asr is None:
        raise HTTPException(status_code=400, detail="ASR 未配置")
    data = await audio.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="音频不能超过 20 MB")
    suffix = Path(audio.filename or "").suffix.lower() or ".wav"
    target = runtime.settings.data_dir / "tmp" / f"webui_asr_{uuid.uuid4().hex}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        text = await runtime.asr.transcribe(target)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"识别失败：{exc}") from exc
    finally:
        target.unlink(missing_ok=True)
    return {"ok": True, "text": text}


@router.get("/logs")
async def logs(lines: int = 200) -> dict[str, Any]:
    lines = max(10, min(2000, int(lines)))
    return {"text": "\n".join(_tail_log(runtime.settings, lines))}


def _tail_log(settings: Settings, lines: int) -> list[str]:
    path = settings.data_dir / "logs" / "fatfish.log"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total
