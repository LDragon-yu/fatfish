#!/usr/bin/env python3
"""归档某个用户的聊天并重置其角色状态。

用法：
    python scripts/archive_chat.py                 # 归档 owner 的聊天（预览）
    python scripts/archive_chat.py --yes           # 备份并执行
    python scripts/archive_chat.py --user-id 2 --yes

迁移内容：
- 归档该用户的 messages/facts/tags/topics/thoughts/state/stats/decisions；
- 清空该用户的聊天、决策和话题，取消其待触发思考；
- 重置该用户的情绪/关系/行为统计；
- user facts/tags 保留在数据库中（归档文件里也有副本）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fatfish import state  # noqa: E402
from fatfish.config import Settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TABLES = (
    "messages",
    "facts",
    "tags",
    "topics",
    "scheduled_thoughts",
    "state",
    "stats",
    "decisions",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="归档聊天并重置某个用户的状态")
    parser.add_argument("--yes", action="store_true", help="真正写入归档并清空")
    parser.add_argument("--user-id", type=int, default=None, help="默认 owner")
    args = parser.parse_args()

    settings = Settings.load()
    if not settings.db_path.exists():
        print(f"数据库不存在：{settings.db_path}")
        return 1
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row

    if args.user_id is not None:
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (int(args.user_id),)
        ).fetchone()
    else:
        user = conn.execute(
            "SELECT * FROM users WHERE role='owner' ORDER BY id ASC LIMIT 1"
        ).fetchone()
    if user is None:
        print("找不到用户；请先迁移多用户结构或指定 --user-id。")
        return 1
    user_id = int(user["id"])

    data = {
        table: [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE user_id=?", (user_id,)
            )
        ]
        for table in TABLES
    }
    print(f"用户：{user['platform_id']}（user_id={user_id}）")
    print("当前数据：")
    for table in TABLES:
        print(f"  {table}: {len(data[table])} 条")

    if not args.yes:
        print("\n这是预览。确认后加 --yes：会先写入归档，再清空该用户的聊天、")
        print("决策和话题，取消其待触发思考，并重置情绪/关系/行为统计。")
        print("该用户的 facts/tags 会保留。")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = settings.data_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"chat-user{user_id}-{stamp}.json"
    payload = {
        "exported_at": time.time(),
        "user": dict(user),
        "db": str(settings.db_path),
        "counts": {table: len(rows) for table, rows in data.items()},
        "tables": data,
    }
    archive_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        archive_path.chmod(0o600)
    except OSError:
        pass
    print(f"已归档：{archive_path}")

    now = time.time()
    with conn:
        conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM decisions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM topics WHERE user_id=?", (user_id,))
        conn.execute(
            "UPDATE scheduled_thoughts SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND status IN ('pending','processing')",
            (now, user_id),
        )
        conn.execute("DELETE FROM state WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM stats WHERE user_id=?", (user_id,))
        for key, value in {
            "mood": state.default_mood(),
            "relationship": state.default_relationship(),
            "last_tick": now,
        }.items():
            conn.execute(
                "INSERT INTO state(user_id,key,value,updated_at) VALUES(?,?,?,?)",
                (user_id, key, json.dumps(value, ensure_ascii=False), now),
            )
        conn.execute(
            "INSERT INTO stats(user_id,key,value,updated_at) VALUES(?,?,?,?)",
            (
                user_id,
                "behavior",
                json.dumps(state.DEFAULT_BEHAVIOR, ensure_ascii=False),
                now,
            ),
        )
    conn.close()
    print("已清空该用户的聊天、决策和话题，取消待触发思考，重置情绪/关系/行为统计。")
    print("facts / tags 已保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
