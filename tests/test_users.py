from __future__ import annotations

import asyncio

import pytest

from fatfish import users
from fatfish.config import Settings
from fatfish.db import Database


def test_unknown_contact_becomes_pending_and_can_activate(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            contact = await users.record_contact(db, "qq", "30001")
            assert contact is not None
            assert contact["status"] == "pending"
            assert not users.is_active(contact)

            activated = await users.set_status(db, int(contact["id"]), "active")
            assert activated["status"] == "active"
            assert users.is_active(activated)

            blocked = await users.set_status(db, int(contact["id"]), "blocked")
            assert blocked["status"] == "blocked"
            assert not users.is_active(blocked)

            again = await users.record_contact(db, "qq", "30001")
            assert again["status"] == "blocked"
            assert await db.count_messages(int(contact["id"])) == 0
        finally:
            await db.close()

    asyncio.run(scenario())


def test_owner_cannot_be_blocked(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            owner = await users.create_user(
                db, "1253206845", role="owner", status="active"
            )
            with pytest.raises(ValueError):
                await users.set_status(db, int(owner["id"]), "blocked")
        finally:
            await db.close()

    asyncio.run(scenario())
