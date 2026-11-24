from __future__ import annotations

import asyncio
import time

from fatfish import state, users
from fatfish.config import Settings
from fatfish.db import Database


def test_decay_moves_toward_baseline(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            mood = state.default_mood()
            mood["joy"] = 100.0
            await db.set_state(user_id, "mood", mood)
            await db.set_state(user_id, "relationship", state.default_relationship())
            await db.set_state(user_id, "last_tick", time.time() - 120 * 60)

            new_mood, _ = await state.tick(
                db, user_id, time.time(), settings.tz
            )
            assert new_mood["joy"] < 100
            assert new_mood["joy"] > 55
        finally:
            await db.close()

    asyncio.run(scenario())


def test_apply_deltas_clamps_and_renders(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001")
            user_id = int(user["id"])
            await state.ensure_ready(db, user_id)
            mood, rel = await state.apply_deltas(
                db,
                user_id,
                {"joy": 200, "energy": -500},
                {"intimacy": 200, "grudge": 1},
            )
            assert mood["joy"] == 100
            assert mood["energy"] == 0
            assert rel["intimacy"] == 100
            assert rel["grudge"] == 1
            assert "高兴" in state.render_mood(mood)
            assert "综合好感" in state.render_relationship(rel)
        finally:
            await db.close()

    asyncio.run(scenario())
