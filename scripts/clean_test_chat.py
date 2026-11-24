#!/usr/bin/env python3
"""删除测试产生的聊天记录。

用法：
    python scripts/clean_test_chat.py --ids 1-3,61-62 --dry-run
    python scripts/clean_test_chat.py --ids 1-3,61-62 --yes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "private" / "fatfish.db"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_ids(spec: str) -> list[int]:
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            ids.extend(range(int(start), int(end) + 1))
        else:
            ids.append(int(part))
    return sorted(set(ids))


def main() -> int:
    parser = argparse.ArgumentParser(description="删除测试产生的聊天记录")
    parser.add_argument("--ids", required=True, help="消息 id，例如 1-3,61-62")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--yes", action="store_true", help="真正执行删除")
    args = parser.parse_args()

    ids = parse_ids(args.ids)
    if not ids:
        print("没有要删除的消息")
        return 0

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, user_id, role, text, ts FROM messages WHERE id IN ({placeholders}) "
        "ORDER BY id",
        ids,
    ).fetchall()
    if not rows:
        print("没有匹配的消息")
        return 0

    print("将删除以下消息：")
    for row in rows:
        text = (row["text"] or "").replace("\n", " ")[:50]
        print(f"  {row['id']} [{row['role']}] {text!r}")
    if not args.yes:
        print("这是预览。加 --yes 才会真正删除。")
        return 0

    user_texts = [
        (int(row["user_id"]), row["text"])
        for row in rows
        if row["role"] == "user" and row["text"]
    ]
    timestamps = [float(row["ts"]) for row in rows if row["ts"] is not None]
    conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
    deleted_decisions = 0
    if timestamps:
        low, high = min(timestamps) - 5, max(timestamps) + 120
        for user_id, text in user_texts:
            cursor = conn.execute(
                "DELETE FROM decisions WHERE user_id=? AND summary=? "
                "AND ts BETWEEN ? AND ?",
                (user_id, text, low, high),
            )
            deleted_decisions += cursor.rowcount
    conn.commit()
    print(f"已删除 {len(rows)} 条消息，{deleted_decisions} 条决策记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
