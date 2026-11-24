from __future__ import annotations

import asyncio
import time

from fatfish import users
from fatfish.config import Settings
from fatfish.context import ContextBuilder
from fatfish.db import Database


def test_context_contains_blocks_and_memory(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            await db.add_message(user_id, "user", "明天上午要面试，有点紧张")
            await db.add_message(user_id, "assistant", "明天几点呀？")
            await db.add_fact(
                user_id,
                "event",
                "11-24 上午面试，他有点紧张",
                topic="面试",
                importance=4,
            )
            await db.upsert_tag(
                user_id,
                "喜欢语音",
                "是",
                confidence=0.8,
                evidence="7/10 条是语音",
            )
            await db.upsert_topic(
                user_id, "面试", importance=4, next_check_at=time.time() + 3600
            )

            builder = ContextBuilder(db, settings)
            messages = await builder.build(
                user_id, "user", user_text="我有点担心面试"
            )
            system = messages[0]["content"]

            assert "[关系]" in system
            assert "[关于他]" in system
            assert "喜欢语音" in system
            assert "[长期记忆]" in system
            assert "[未完成话题]" in system
            assert "面试" in system
            assert "[现在]" in system
            assert messages[-1]["role"] == "user"
            assert sum(
                1
                for m in messages
                if m["role"] == "user"
                and "我有点担心面试" in str(m["content"])
            ) == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_proactive_context_has_cue_and_no_duplicate_user(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            await db.add_message(user_id, "user", "今天好累")
            await db.add_message(user_id, "assistant", "那早点休息")
            thought = {"id": 1, "reason": "看看他面试怎么样", "topic": "面试"}

            builder = ContextBuilder(db, settings)
            messages = await builder.build(user_id, "proactive", thought=thought)
            assert messages[-1]["role"] == "user"
            assert "系统触发" in messages[-1]["content"]
            assert "不是他发来的消息" in messages[-1]["content"]
            assert "面试" in messages[-1]["content"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_attaches_images_and_video_frames(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            await db.add_message(user_id, "user", "[图片] 你看")
            media_dir = tmp_path / "media" / "images"
            media_dir.mkdir(parents=True)
            (media_dir / "a.jpg").write_bytes(b"fake-jpeg")
            frames_dir = tmp_path / "media" / "frames"
            frames_dir.mkdir(parents=True)
            (frames_dir / "f1.jpg").write_bytes(b"fake-frame")

            builder = ContextBuilder(db, settings)
            messages = await builder.build(
                user_id,
                "user",
                user_text="[图片] 你看",
                media=[
                    {"kind": "image", "path": "media/images/a.jpg"},
                    {
                        "kind": "video",
                        "path": "media/videos/v.mp4",
                        "frames": ["media/frames/f1.jpg"],
                        "transcript": "他说今天吃面",
                        "duration": 6.4,
                    },
                ],
            )
            last = messages[-1]
            assert last["role"] == "user"
            assert isinstance(last["content"], list)
            assert last["content"][0]["type"] == "text"
            image_parts = [p for p in last["content"] if p["type"] == "image_url"]
            assert len(image_parts) == 2
            assert all(
                p["image_url"]["url"].startswith("data:image/jpeg;base64,")
                for p in image_parts
            )
            texts = " ".join(
                p.get("text", "") for p in last["content"] if p["type"] == "text"
            )
            assert "音轨转写：他说今天吃面" in texts
        finally:
            await db.close()

    asyncio.run(scenario())
