from __future__ import annotations

import asyncio

from fatfish import prompts, users, voices
from fatfish.config import Settings
from fatfish.context import ContextBuilder
from fatfish.db import Database


def test_prompts_are_isolated_per_user(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            first = await users.create_user(db, "10001")
            second = await users.create_user(db, "10002")
            prompts.save_prompt(
                settings, "persona", "你是甲，只在第一个用户的聊天里出现。",
                int(first["id"]),
            )
            prompts.save_prompt(
                settings, "persona", "你是乙，只在第二个用户的聊天里出现。",
                int(second["id"]),
            )

            text_first = prompts.stable_system(
                settings, user_id=int(first["id"])
            )
            text_second = prompts.stable_system(
                settings, user_id=int(second["id"])
            )
            assert "你是甲" in text_first
            assert "你是乙" not in text_first
            assert "你是乙" in text_second
            assert "你是甲" not in text_second
            assert prompts.is_customized(
                settings, "persona", int(first["id"])
            )
            assert not prompts.is_customized(
                settings, "persona", int(second["id"]) + 100
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_uses_user_bot_name(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "10001", bot_name="小乙")
            user_id = int(user["id"])
            messages = await ContextBuilder(db, settings).build(
                user_id, "user", user_text="在吗"
            )
            assert "你是小乙" in messages[0]["content"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_owner_bot_name_survives_ensure_owner(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            owner = await users.create_user(
                db, "10001", role="owner", status="active", bot_name="旧名字"
            )
            settings.owner_qq = "10001"
            updated = await users.ensure_owner(db, settings)
            assert updated is not None
            assert updated["bot_name"] == "旧名字"

            changed = await users.update_profile(
                db, int(owner["id"]), bot_name="新名字"
            )
            assert changed["bot_name"] == "新名字"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_voice_profiles_are_isolated_per_user(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            first = await users.create_user(db, "10001")
            second = await users.create_user(db, "10002")
            first_id = int(first["id"])
            second_id = int(second["id"])

            first_profile = await voices.create_profile(
                db, first_id, name="甲的音色", provider="mimo",
                mode="preset", voice_name="甲", active=True,
            )
            second_profile = await voices.create_profile(
                db, second_id, name="乙的音色", provider="mimo",
                mode="preset", voice_name="乙", active=True,
            )

            active_first = await voices.get_active(db, first_id)
            active_second = await voices.get_active(db, second_id)
            assert active_first is not None
            assert active_first["id"] == first_profile["id"]
            assert active_second is not None
            assert active_second["id"] == second_profile["id"]

            listed_first = await voices.list_profiles(db, first_id)
            assert [item["id"] for item in listed_first] == [first_profile["id"]]

            await voices.set_active(db, second_profile["id"])
            assert (await voices.get_active(db, first_id))["id"] == first_profile["id"]
            assert (await voices.get_active(db, second_id))["id"] == second_profile["id"]
        finally:
            await db.close()

    asyncio.run(scenario())
