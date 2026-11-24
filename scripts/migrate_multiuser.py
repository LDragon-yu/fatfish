#!/usr/bin/env python3
"""把单用户数据库迁移到多用户（多租户）结构。

用法：
    python scripts/migrate_multiuser.py          # 预览
    python scripts/migrate_multiuser.py --yes    # 备份并执行迁移

迁移内容：
- 新建 users 表，并把 OWNER_QQ 写成 owner；
- messages/facts/tags/topics/state/stats/scheduled_thoughts/decisions/voice_profiles 加 user_id；
- 现有数据全部归到 owner（user_id=1）；
- 重建 messages/state/stats/tags/topics 的主键或唯一约束；
- 迁移前自动备份数据库到 private/backup/。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fatfish.config import Settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NEW_MESSAGES = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'text',
    source_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(user_id, source_id)
)
"""

NEW_TOPICS = """
CREATE TABLE topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    importance INTEGER NOT NULL DEFAULT 3,
    last_mentioned REAL,
    next_check_at REAL,
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(user_id, name)
)
"""

NEW_TAGS = """
CREATE TABLE tags (
    user_id INTEGER NOT NULL DEFAULT 1,
    tag TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'model',
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, tag)
)
"""

NEW_STATE = """
CREATE TABLE state (
    user_id INTEGER NOT NULL DEFAULT 1,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, key)
)
"""

NEW_STATS = """
CREATE TABLE stats (
    user_id INTEGER NOT NULL DEFAULT 1,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, key)
)
"""


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def backup(conn: sqlite3.Connection, db_path: Path) -> Path:
    backup_dir = db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-multiuser-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def migrate(conn: sqlite3.Connection, owner_qq: str, bot_name: str) -> None:
    now = time.time()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'qq',
            platform_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'pending',
            bot_name TEXT NOT NULL DEFAULT '',
            proactive_enabled INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_contact_at REAL,
            UNIQUE(platform, platform_id)
        );
        CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
        """
    )
    owner = conn.execute(
        "SELECT id FROM users WHERE platform='qq' AND platform_id=?", (owner_qq,)
    ).fetchone()
    if owner is None:
        cur = conn.execute(
            "INSERT INTO users(platform,platform_id,display_name,role,status,bot_name,"
            "created_at,updated_at,last_contact_at) VALUES('qq',?,?,?,?,?,?,?,?)",
            (owner_qq, "", "owner", "active", bot_name, now, now, now),
        )
        owner_id = int(cur.lastrowid)
    else:
        owner_id = int(owner[0])
    conn.execute(
        "UPDATE users SET role='owner', status='active', bot_name=?, updated_at=? "
        "WHERE id=?",
        (bot_name, now, owner_id),
    )

    # messages：重建以支持 (user_id, source_id) 唯一
    conn.execute("DROP TRIGGER IF EXISTS messages_ai")
    conn.execute("DROP TRIGGER IF EXISTS messages_ad")
    conn.execute("DROP TRIGGER IF EXISTS messages_au")
    conn.execute("ALTER TABLE messages RENAME TO messages_old")
    conn.execute(NEW_MESSAGES)
    conn.execute(
        "INSERT INTO messages(id,user_id,ts,role,text,channel,source_id,meta) "
        "SELECT id,?,ts,role,text,channel,source_id,meta FROM messages_old",
        (owner_id,),
    )
    conn.execute("DROP TABLE messages_old")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON messages(user_id, ts);
        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
        END;
        CREATE TRIGGER messages_au AFTER UPDATE OF text ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END;
        INSERT INTO messages_fts(messages_fts) VALUES('rebuild');
        """
    )

    # facts：加列即可
    if not column_exists(conn, "facts", "user_id"):
        conn.execute(
            "ALTER TABLE facts ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1"
        )
        conn.execute(f"UPDATE facts SET user_id=?", (owner_id,))
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_user_active ON facts(user_id, active)"
    )

    # topics：重建唯一约束
    conn.execute("ALTER TABLE topics RENAME TO topics_old")
    conn.execute(NEW_TOPICS)
    conn.execute(
        "INSERT INTO topics(id,user_id,name,status,importance,last_mentioned,"
        "next_check_at,meta) "
        "SELECT id,?,name,status,importance,last_mentioned,next_check_at,meta "
        "FROM topics_old",
        (owner_id,),
    )
    conn.execute("DROP TABLE topics_old")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_topics_user_status ON topics(user_id, status)"
    )

    # tags：重建主键
    conn.execute("ALTER TABLE tags RENAME TO tags_old")
    conn.execute(NEW_TAGS)
    conn.execute(
        "INSERT INTO tags(user_id,tag,value,confidence,evidence,source,updated_at) "
        "SELECT ?,tag,value,confidence,evidence,source,updated_at FROM tags_old",
        (owner_id,),
    )
    conn.execute("DROP TABLE tags_old")

    # state / stats：重建复合主键
    conn.execute("ALTER TABLE state RENAME TO state_old")
    conn.execute(NEW_STATE)
    conn.execute(
        "INSERT INTO state(user_id,key,value,updated_at) "
        "SELECT ?,key,value,updated_at FROM state_old",
        (owner_id,),
    )
    conn.execute("DROP TABLE state_old")

    conn.execute("ALTER TABLE stats RENAME TO stats_old")
    conn.execute(NEW_STATS)
    conn.execute(
        "INSERT INTO stats(user_id,key,value,updated_at) "
        "SELECT ?,key,value,updated_at FROM stats_old",
        (owner_id,),
    )
    conn.execute("DROP TABLE stats_old")

    # 其余表加列
    for table in ("scheduled_thoughts", "decisions", "voice_profiles"):
        if not column_exists(conn, table, "user_id"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1"
            )
            conn.execute(f"UPDATE {table} SET user_id=?", (owner_id,))

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_thoughts_user_due
            ON scheduled_thoughts(user_id, status, due_at);
        CREATE INDEX IF NOT EXISTS idx_decisions_user_ts
            ON decisions(user_id, ts);
        CREATE INDEX IF NOT EXISTS idx_voice_user_active
            ON voice_profiles(user_id, active, archived);
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="单用户库迁移到多用户结构")
    parser.add_argument("--yes", action="store_true", help="备份并执行迁移")
    args = parser.parse_args()

    settings = Settings.load()
    owner_qq = settings.owner_qq.strip()
    if not owner_qq:
        print("OWNER_QQ 为空，无法确定 owner 用户，先填写后再迁移。")
        return 1
    if not settings.db_path.exists():
        print(f"数据库不存在：{settings.db_path}")
        return 1

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        if table_exists(conn, "users") and count(conn, "users") > 0:
            print("users 表已存在且有数据，疑似已迁移，中止。")
            return 0
        if column_exists(conn, "messages", "user_id"):
            print("messages 已有 user_id，疑似已迁移，中止。")
            return 0

        print("迁移前数据：")
        for table in (
            "messages",
            "facts",
            "tags",
            "topics",
            "scheduled_thoughts",
            "state",
            "stats",
            "decisions",
            "voice_profiles",
        ):
            if table_exists(conn, table):
                print(f"  {table}: {count(conn, table)} 条")
        print(f"owner: {owner_qq}，角色名: {settings.bot_name}")

        if not args.yes:
            print("\n这是预览。加 --yes 会先备份数据库，再执行迁移。")
            return 0

        backup_path = backup(conn, settings.db_path)
        print(f"已备份：{backup_path}")
        with conn:
            migrate(conn, owner_qq, settings.bot_name)
        print("迁移完成：")
        print(f"  users: {count(conn, 'users')} 条")
        for table in ("messages", "facts", "tags", "topics", "state", "stats"):
            print(f"  {table}: {count(conn, table)} 条")
        print(f"  messages.user_id 列: {column_exists(conn, 'messages', 'user_id')}")
        print("现在可以启动新版本。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
