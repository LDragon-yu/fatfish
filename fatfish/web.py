from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import state, users
from .admin import router as admin_router
from .runtime import runtime

logger = logging.getLogger(__name__)
WEBUI_DIR = Path(__file__).resolve().parent / "webui"


async def _resolve_web_user(user_id: int | None):
    db = runtime.db
    user = await users.get_user(db, int(user_id)) if user_id else await users.owner(db)
    if user is None or user.get("status") != "active":
        raise HTTPException(status_code=400, detail="用户不存在或未启用")
    return user


async def _state_payload(user_id: int) -> dict:
    db = runtime.db
    mood, rel = await state.load_state(db, user_id)
    tags = await db.list_tags(user_id)
    topics = await db.list_topics(user_id, ("open", "waiting"))
    thoughts = await db.list_pending_thoughts(user_id, 10)
    behavior = await state.get_behavior(db, user_id)
    return {
        "mood": mood,
        "relationship": rel,
        "overall": state.overall(rel),
        "level": state.level(state.overall(rel)),
        "tags": tags,
        "topics": topics,
        "thoughts": thoughts,
        "behavior": behavior,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="fatfish", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")
app.include_router(admin_router)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEBUI_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    settings = runtime.settings
    return {
        "ok": True,
        "bot_name": settings.bot_name,
        "mock": settings.mock_llm,
        "model": settings.deepseek_model,
        "vision_model": settings.vision_model,
        "db": str(settings.db_path),
        "tts": runtime.tts is not None,
        "asr": runtime.asr is not None,
        "onebot": runtime.onebot.status(),
    }


@app.get("/api/state")
async def api_state(user_id: int | None = None) -> dict:
    user = await _resolve_web_user(user_id)
    return await _state_payload(int(user["id"]))


@app.get("/api/history")
async def api_history(user_id: int | None = None) -> dict:
    user = await _resolve_web_user(user_id)
    rows = await runtime.db.recent_messages(int(user["id"]), 120)
    return {"messages": [_message_json(row) for row in rows]}


def _message_json(row: dict) -> dict:
    try:
        meta = json.loads(row.get("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    audio_path = meta.get("audio_path")
    audio_url = f"/api/audio/{Path(audio_path).name}" if audio_path else None
    return {
        "role": row.get("role"),
        "text": row.get("text") or "",
        "channel": row.get("channel") or "text",
        "ts": row.get("ts") or 0,
        "audio_url": audio_url,
        "tts_error": meta.get("tts_error"),
    }


@app.get("/api/audio/{name}")
async def api_audio(name: str):
    root = (runtime.settings.data_dir / "voice" / "out" / "tts").resolve()
    path = (root / Path(name).name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/wav")


@app.websocket("/onebot/ws")
async def onebot_endpoint(websocket: WebSocket) -> None:
    await runtime.onebot.handle(websocket)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    raw_user_id = websocket.query_params.get("user_id")
    try:
        requested_id = int(raw_user_id) if raw_user_id else None
    except ValueError:
        requested_id = None
    db = runtime.db
    user = (
        await users.get_user(db, requested_id)
        if requested_id is not None
        else await users.owner(db)
    )
    if user is None or user.get("status") != "active":
        await websocket.close(code=1008, reason="user not active")
        return
    user_id = int(user["id"])

    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send_json(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    rows = await runtime.db.recent_messages(user_id, 80)
    await send_json({"type": "history", "messages": [_message_json(row) for row in rows]})

    queue = runtime.engine.bus.subscribe()

    async def forward() -> None:
        try:
            while True:
                result = await queue.get()
                if int(result.user_id) != user_id:
                    continue
                for item in result.outgoing:
                    if item.delay:
                        await asyncio.sleep(min(max(item.delay, 0.0), 3.0))
                    audio_path = item.meta.get("audio_path")
                    audio_url = (
                        f"/api/audio/{Path(audio_path).name}" if audio_path else None
                    )
                    await send_json(
                        {
                            "type": "assistant",
                            "message": {
                                "kind": item.kind,
                                "text": item.text,
                                "voice": item.voice,
                                "delay": item.delay,
                                "audio_url": audio_url,
                                "tts_error": item.meta.get("tts_error"),
                            },
                        }
                    )
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            return

    forward_task = asyncio.create_task(forward(), name="fatfish-ws-forward")

    try:
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")
            if kind == "message":
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                source_id = str(data.get("id") or uuid4().hex)
                try:
                    result = await runtime.engine.handle_user_message(
                        user_id, text, source_id=source_id
                    )
                    if result.duplicate:
                        await send_json({"type": "notice", "message": "（重复消息已忽略）"})
                except Exception as exc:  # noqa: BLE001 - report to the browser
                    logger.exception("处理消息失败")
                    await send_json({"type": "error", "message": str(exc)})
            elif kind == "state":
                await send_json(
                    {"type": "state", "state": await _state_payload(user_id)}
                )
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            pass
        runtime.engine.bus.unsubscribe(queue)
