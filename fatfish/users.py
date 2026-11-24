from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .db import Database


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["platform_id"] = str(data.get("platform_id") or "")
    data["bot_name"] = str(data.get("bot_name") or "")
    data["display_name"] = str(data.get("display_name") or "")
    return data


async def get_user(db: Database, user_id: int) -> dict[str, Any] | None:
    return _row(await db.fetchone("SELECT * FROM users WHERE id=?", (int(user_id),)))


async def resolve(
    db: Database, platform: str, platform_id: str
) -> dict[str, Any] | None:
    return _row(
        await db.fetchone(
            "SELECT * FROM users WHERE platform=? AND platform_id=?",
            (platform, str(platform_id)),
        )
    )


async def owner(db: Database) -> dict[str, Any] | None:
    return _row(
        await db.fetchone(
            "SELECT * FROM users WHERE role='owner' AND status='active' "
            "ORDER BY id ASC LIMIT 1"
        )
    )


async def active_users(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM users WHERE status='active' ORDER BY id ASC"
    )
    return [_row(row) for row in rows]  # type: ignore[misc]


async def ensure_owner(db: Database, settings: Settings) -> dict[str, Any] | None:
    platform_id = settings.owner_qq.strip()
    if not platform_id:
        return None
    row = await resolve(db, "qq", platform_id)
    now = time.time()
    if row is None:
        cursor = await db.execute(
            "INSERT INTO users(platform,platform_id,display_name,role,status,bot_name,"
            "created_at,updated_at,last_contact_at) VALUES('qq',?,?,?,?,?,?,?,?)",
            (
                platform_id,
                "",
                "owner",
                "active",
                settings.bot_name,
                now,
                now,
                now,
            ),
        )
        return await get_user(db, int(cursor.lastrowid or 0))
    await db.execute(
        "UPDATE users SET role='owner', status='active', updated_at=? "
        "WHERE id=?",
        (now, int(row["id"])),
    )
    return await get_user(db, int(row["id"]))


async def update_profile(
    db: Database,
    user_id: int,
    *,
    bot_name: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    user = await get_user(db, user_id)
    if user is None:
        raise KeyError("用户不存在")
    updates: dict[str, Any] = {"updated_at": time.time()}
    if bot_name is not None:
        updates["bot_name"] = str(bot_name).strip()[:32]
    if display_name is not None:
        updates["display_name"] = str(display_name).strip()[:64]
    assignments = ",".join(f"{key}=?" for key in updates)
    await db.execute(
        f"UPDATE users SET {assignments} WHERE id=?",
        (*updates.values(), int(user_id)),
    )
    updated = await get_user(db, user_id)
    assert updated is not None
    return updated


async def record_contact(
    db: Database, platform: str, platform_id: str
) -> dict[str, Any] | None:
    """Record an unknown/pending contact without storing the message body."""
    now = time.time()
    row = await resolve(db, platform, str(platform_id))
    if row is None:
        cursor = await db.execute(
            "INSERT INTO users(platform,platform_id,display_name,role,status,bot_name,"
            "created_at,updated_at,last_contact_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (platform, str(platform_id), "", "user", "pending", "", now, now, now),
        )
        return await get_user(db, int(cursor.lastrowid or 0))
    await db.execute(
        "UPDATE users SET last_contact_at=?, updated_at=? WHERE id=?",
        (now, now, int(row["id"])),
    )
    return row


async def list_users(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM messages m WHERE m.user_id=u.id) AS message_count, "
        "(SELECT COUNT(*) FROM scheduled_thoughts t "
        " WHERE t.user_id=u.id AND t.status='pending') AS pending_thoughts "
        "FROM users u "
        "ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
        "u.id ASC"
    )
    return [_row(row) for row in rows]  # type: ignore[misc]


async def create_user(
    db: Database,
    platform_id: str,
    *,
    display_name: str = "",
    role: str = "user",
    status: str = "active",
    bot_name: str = "",
) -> dict[str, Any]:
    platform_id = str(platform_id).strip()
    if not platform_id:
        raise ValueError("platform_id 不能为空")
    existing = await resolve(db, "qq", platform_id)
    if existing is not None:
        return existing
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO users(platform,platform_id,display_name,role,status,bot_name,"
        "created_at,updated_at) VALUES('qq',?,?,?,?,?,?,?)",
        (platform_id, display_name, role, status, bot_name, now, now),
    )
    created = await get_user(db, int(cursor.lastrowid or 0))
    assert created is not None
    return created


async def set_status(
    db: Database, user_id: int, status: str
) -> dict[str, Any]:
    if status not in {"pending", "active", "blocked"}:
        raise ValueError("status 只能是 pending/active/blocked")
    user = await get_user(db, user_id)
    if user is None:
        raise KeyError("用户不存在")
    if user["role"] == "owner" and status != "active":
        raise ValueError("不能禁用 owner")
    await db.execute(
        "UPDATE users SET status=?, updated_at=? WHERE id=?",
        (status, time.time(), int(user_id)),
    )
    updated = await get_user(db, user_id)
    assert updated is not None
    return updated


def is_active(user: dict[str, Any] | None) -> bool:
    return bool(user) and user.get("status") == "active"


def bot_name_for(user: dict[str, Any] | None, settings: Settings) -> str:
    if user and user.get("bot_name"):
        return str(user["bot_name"])
    return settings.bot_name
