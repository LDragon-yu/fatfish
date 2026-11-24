from __future__ import annotations

import asyncio
import json
import logging

from . import state, users
from .config import Settings
from .context import ContextBuilder
from .db import Database
from .engine import Engine
from .llm import build_llm
from .scheduler import Scheduler
from .tts import build_tts


def _print_outgoing(result, bot_name: str) -> None:
    for item in result.outgoing:
        if item.kind == "voice":
            voice = item.voice or "siliconflow"
            print(f"\n{bot_name} [语音/{voice}]> {item.text}\n")
            if item.meta.get("audio_path"):
                print(f"音频：{item.meta['audio_path']}\n")
            if item.meta.get("tts_error"):
                print(f"（TTS 失败，回退文字：{item.meta['tts_error']}）\n")
        else:
            print(f"\n{bot_name}> {item.text}\n")


async def _printer(engine: Engine, bot_name: str) -> None:
    queue = engine.bus.subscribe()
    try:
        while True:
            result = await queue.get()
            _print_outgoing(result, bot_name)
    finally:
        engine.bus.unsubscribe(queue)


async def _show_state(db: Database, user_id: int) -> None:
    mood, rel = await state.load_state(db, user_id)
    tags = await db.list_tags(user_id)
    topics = await db.list_topics(user_id, ("open", "waiting"))
    thoughts = await db.list_pending_thoughts(user_id, 10)
    behavior = await state.get_behavior(db, user_id)
    print("\n[状态]")
    print("心情：", state.render_mood(mood))
    print("关系：", state.render_relationship(rel))
    print("标签：", json.dumps(tags, ensure_ascii=False, indent=2))
    print("话题：", json.dumps(topics, ensure_ascii=False, indent=2))
    print("待触发：", json.dumps(thoughts, ensure_ascii=False, indent=2))
    print("行为统计：", json.dumps(behavior, ensure_ascii=False, indent=2))
    print()


async def main() -> None:
    settings = Settings.load()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))

    db = Database(settings.db_path)
    await db.connect()
    owner = await users.ensure_owner(db, settings)
    if owner is None:
        print("没有配置 OWNER_QQ，无法使用 CLI")
        await db.close()
        return
    user_id = int(owner["id"])
    await state.ensure_ready(db, user_id)

    llm = build_llm(settings)
    context = ContextBuilder(db, settings)
    tts = build_tts(settings)
    engine = Engine(db, settings, llm, context, tts=tts)
    scheduler = Scheduler(engine, db, settings)
    if settings.proactive_enabled:
        await scheduler.start()
    printer_task = asyncio.create_task(
        _printer(engine, settings.bot_name), name="fatfish-cli-printer"
    )

    mode = "mock" if settings.mock_llm else settings.deepseek_model
    print(f"fatfish CLI · 模型：{mode} · 数据库：{settings.db_path}")
    print("输入 /help 查看命令；/quit 退出。\n")

    try:
        while True:
            try:
                line = await asyncio.to_thread(input, "你> ")
            except (EOFError, KeyboardInterrupt):
                break
            text = line.strip()
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                break
            if text == "/help":
                print(
                    "/state  查看心情、关系、标签、话题、待触发\n"
                    "/topics 查看未完成话题\n"
                    "/tags   查看行为标签\n"
                    "/thoughts 查看待触发\n"
                    "/quit   退出\n"
                )
                continue
            if text == "/state":
                await _show_state(db, user_id)
                continue
            if text == "/topics":
                print(json.dumps(await db.list_topics(user_id, ("open", "waiting")), ensure_ascii=False, indent=2))
                continue
            if text == "/tags":
                print(json.dumps(await db.list_tags(user_id), ensure_ascii=False, indent=2))
                continue
            if text == "/thoughts":
                print(json.dumps(await db.list_pending_thoughts(user_id, 20), ensure_ascii=False, indent=2))
                continue

            result = await engine.handle_user_message(user_id, text)
            if result.duplicate:
                print("（重复消息，已忽略）")
            if result.error:
                print(f"[模型错误] {result.error}")
            await asyncio.sleep(0.05)
    finally:
        printer_task.cancel()
        try:
            await printer_task
        except asyncio.CancelledError:
            pass
        await scheduler.stop()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
