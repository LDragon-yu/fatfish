from __future__ import annotations

import asyncio
import time

from fatfish import users
from fatfish.config import Settings
from fatfish.db import Database


def _run(tmp_path, coro_fn):
    async def runner():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001", display_name="测试用户")
            await coro_fn(db, settings, int(user["id"]))
        finally:
            await db.close()

    asyncio.run(runner())


def test_messages_dedup_recent_and_search(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        first = await db.add_message(
            user_id, "user", "明天上午要面试，有点紧张", source_id="m1"
        )
        second = await db.add_message(
            user_id, "assistant", "明天几点呀？", source_id="m2"
        )
        duplicate = await db.add_message(
            user_id, "user", "重复", source_id="m1"
        )
        assert first > 0 and second > first
        assert duplicate == first
        assert await db.count_messages(user_id) == 2

        recent = await db.recent_messages(user_id, 10)
        assert [row["role"] for row in recent] == ["user", "assistant"]

        hits = await db.search_messages(user_id, "面试")
        assert hits and "面试" in hits[0]["text"]

        hits2 = await db.search_messages(user_id, "你觉得我面试能过吗")
        assert hits2 and "面试" in hits2[0]["text"]

    _run(tmp_path, scenario)


def test_facts_tags_and_forget(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        await db.add_fact(
            user_id, "event", "11-24 上午面试，他说有点紧张", topic="面试", importance=4
        )
        await db.add_fact(user_id, "preference", "他喜欢语音", importance=3)
        hits = await db.search_facts(user_id, "面试怎么样")
        assert hits and "面试" in hits[0]["content"]

        await db.upsert_tag(
            user_id, "喜欢语音", "是", confidence=0.8, evidence="7/10 条是语音"
        )
        tags = await db.list_tags(user_id)
        assert tags[0]["tag"] == "喜欢语音"

        removed = await db.deactivate_facts(user_id, "面试")
        assert removed == 1
        assert all(
            "面试" not in f["content"] for f in await db.list_facts(user_id)
        )

    _run(tmp_path, scenario)


def test_topics_and_thoughts(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        now = time.time()
        await db.upsert_topic(
            user_id, "面试", status="open", importance=4, last_mentioned=now
        )
        due_at = now - 1
        thought_id = await db.schedule_thought(
            user_id, due_at, "看看话题：面试", topic="面试"
        )

        assert (await db.get_topic(user_id, "面试"))["status"] == "open"
        due = await db.due_thoughts(user_id, now)
        assert [row["id"] for row in due] == [thought_id]

        await db.mark_thought(thought_id, "done")
        assert await db.due_thoughts(user_id, now) == []

        await db.upsert_topic(
            user_id, "面试", status="closed", importance=4, next_check_at=None
        )
        await db.cancel_thoughts_for_topic(user_id, "面试")
        assert (await db.get_topic(user_id, "面试"))["status"] == "closed"

    _run(tmp_path, scenario)


def test_upsert_thought_for_topic_dedupes(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        now = time.time()
        first = await db.upsert_thought_for_topic(
            user_id, now + 60, "看看面试", "面试"
        )
        second = await db.upsert_thought_for_topic(
            user_id, now + 120, "再看面试", "面试"
        )
        assert first == second
        rows = [
            r
            for r in await db.list_pending_thoughts(user_id)
            if r["topic"] == "面试"
        ]
        assert len(rows) == 1
        assert rows[0]["due_at"] == now + 120

    _run(tmp_path, scenario)


def test_user_isolation(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        other = await users.create_user(db, "10002", display_name="另一个用户")
        other_id = int(other["id"])
        await db.add_message(user_id, "user", "用户一的秘密")
        await db.add_message(other_id, "user", "用户二的秘密")
        await db.add_fact(user_id, "fact", "用户一的事实")
        await db.add_fact(other_id, "fact", "用户二的事实")
        await db.upsert_tag(user_id, "习惯", "早睡")
        await db.upsert_tag(other_id, "习惯", "晚睡")

        assert [r["text"] for r in await db.recent_messages(user_id)] == [
            "用户一的秘密"
        ]
        assert [r["text"] for r in await db.recent_messages(other_id)] == [
            "用户二的秘密"
        ]
        assert await db.search_messages(other_id, "秘密") != []
        assert all(
            "用户二" not in r["text"]
            for r in await db.search_messages(user_id, "秘密")
        )
        assert [f["content"] for f in await db.list_facts(user_id)] == [
            "用户一的事实"
        ]
        assert [t["value"] for t in await db.list_tags(user_id)] == ["早睡"]

    _run(tmp_path, scenario)


def test_memory_admin_crud(tmp_path):
    async def scenario(db: Database, settings: Settings, user_id: int):
        fact_id = await db.add_fact(user_id, "fact", "旧事实")
        facts = await db.list_facts(user_id, include_inactive=True)
        assert facts[0]["content"] == "旧事实"
        updated = await db.update_fact(
            user_id, fact_id, content="新事实", importance=5, active=False
        )
        assert updated["content"] == "新事实"
        assert updated["active"] == 0
        assert await db.delete_fact(user_id, fact_id) == 1

        await db.upsert_tag(user_id, "测试标签", "值", confidence=0.8)
        assert await db.delete_tag(user_id, "测试标签") == 1

        await db.upsert_topic(user_id, "测试话题", importance=4)
        assert await db.delete_topic(user_id, "测试话题") == 1

        thought_id = await db.schedule_thought(
            user_id, time.time() + 60, "测试待触发"
        )
        assert any(t["id"] == thought_id for t in await db.list_thoughts(user_id))
        thought = await db.update_thought(
            user_id, thought_id, status="cancelled"
        )
        assert thought["status"] == "cancelled"
        assert await db.delete_thought(user_id, thought_id) == 1

        message_id = await db.add_message(user_id, "user", "可删除消息")
        assert await db.delete_message(user_id, message_id) == 1
        assert await db.count_messages(user_id) == 0

    _run(tmp_path, scenario)
