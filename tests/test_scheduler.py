from __future__ import annotations

import asyncio
import time

from fatfish import users
from fatfish.config import Settings
from fatfish.db import Database
from fatfish.scheduler import Scheduler


class FakeEngine:
    def __init__(self, precheck=None):
        self.calls = []
        self.precheck = precheck

    @property
    def is_busy(self):
        return False

    async def precheck_thought(self, user_id, thought):
        if self.precheck is None:
            return {"action": "fire"}
        return self.precheck

    async def handle_proactive(self, user_id, thought):
        self.calls.append((user_id, thought))


def test_run_once_processes_due_thought(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        settings.proactive_enabled = True
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            thought_id = await db.schedule_thought(
                user_id, time.time() - 5, "问问他面试"
            )
            engine = FakeEngine()
            scheduler = Scheduler(engine, db, settings)

            processed = await scheduler.run_once()
            assert processed == 1
            assert len(engine.calls) == 1
            assert engine.calls[0][0] == user_id
            assert engine.calls[0][1]["id"] == thought_id

            row = (
                await db.fetchall(
                    "SELECT * FROM scheduled_thoughts WHERE id=?", (thought_id,)
                )
            )[0]
            assert row["status"] == "done"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_seed_only_when_history_exists_and_no_pending(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        settings.proactive_enabled = True
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            engine = FakeEngine()
            scheduler = Scheduler(engine, db, settings)
            await scheduler._seed_if_needed()
            assert await db.count_pending_thoughts(user_id) == 0

            await db.add_message(user_id, "user", "你好")
            await scheduler._seed_if_needed()
            assert await db.count_pending_thoughts(user_id) == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_precheck_cancel_skips_thought(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        settings.proactive_enabled = True
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            thought_id = await db.schedule_thought(
                user_id, time.time() - 5, "问问组会"
            )
            engine = FakeEngine(
                precheck={"action": "cancel", "reason": "下午已经聊过组会"}
            )
            scheduler = Scheduler(engine, db, settings)

            processed = await scheduler.run_once()
            assert processed == 0
            assert engine.calls == []
            row = (
                await db.fetchall(
                    "SELECT * FROM scheduled_thoughts WHERE id=?", (thought_id,)
                )
            )[0]
            assert row["status"] == "cancelled"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_precheck_revise_updates_thought(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        settings.proactive_enabled = True
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            thought_id = await db.schedule_thought(
                user_id, time.time() - 5, "提醒关灯，顺便问问组会"
            )
            engine = FakeEngine(
                precheck={
                    "action": "revise",
                    "reason": "组会已经聊过",
                    "new_reason": "只提醒他关灯",
                    "new_topic": "",
                }
            )
            scheduler = Scheduler(engine, db, settings)

            processed = await scheduler.run_once()
            assert processed == 1
            assert engine.calls
            assert engine.calls[0][1]["reason"] == "只提醒他关灯"
            row = (
                await db.fetchall(
                    "SELECT * FROM scheduled_thoughts WHERE id=?", (thought_id,)
                )
            )[0]
            assert row["status"] == "done"
            assert row["reason"] == "只提醒他关灯"
        finally:
            await db.close()

    asyncio.run(scenario())
