from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;

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

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'text',
    source_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(user_id, source_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON messages(user_id, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    content='messages',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    ts REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    topic TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 3,
    source_message_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_facts_user_active ON facts(user_id, active);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content,
    content='facts',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF content ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    importance INTEGER NOT NULL DEFAULT 3,
    last_mentioned REAL,
    next_check_at REAL,
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_topics_user_status ON topics(user_id, status);

CREATE TABLE IF NOT EXISTS tags (
    user_id INTEGER NOT NULL DEFAULT 1,
    tag TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'model',
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, tag)
);

CREATE TABLE IF NOT EXISTS state (
    user_id INTEGER NOT NULL DEFAULT 1,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, key)
);

CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER NOT NULL DEFAULT 1,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, key)
);

CREATE TABLE IF NOT EXISTS scheduled_thoughts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    due_at REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thoughts_user_due ON scheduled_thoughts(user_id, status, due_at);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    ts REAL NOT NULL,
    trigger TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_decisions_user_ts ON decisions(user_id, ts);

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'siliconflow',
    mode TEXT NOT NULL DEFAULT 'references',
    model TEXT NOT NULL DEFAULT '',
    reference_path TEXT NOT NULL DEFAULT '',
    reference_text TEXT NOT NULL DEFAULT '',
    speed REAL NOT NULL DEFAULT 0.8,
    voice_uri TEXT NOT NULL DEFAULT '',
    voice_name TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_user_active ON voice_profiles(user_id, active, archived);
"""

_CJK = re.compile(r"[0-9A-Za-z一-鿿]+")


def _norm(text: str) -> str:
    return "".join(_CJK.findall(str(text or "").lower()))


def _grams(text: str, n: int) -> set[str]:
    s = _norm(text)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _overlap(query: str, text: str) -> float:
    q = _grams(query, 2)
    t = _grams(text, 2)
    return len(q & t) / (len(q) or 1)


def _fts_or_query(query: str, n: int = 3, max_terms: int = 24) -> str | None:
    s = _norm(query)
    if len(s) < n:
        return None
    seen: list[str] = []
    for i in range(len(s) - n + 1):
        gram = s[i : i + n]
        if gram not in seen:
            seen.append(gram)
    if not seen:
        return None
    return " OR ".join(f'"{g}"' for g in seen[:max_terms])


def _bigrams(query: str, max_terms: int = 24) -> list[str]:
    s = _norm(query)
    out: list[str] = []
    for i in range(max(0, len(s) - 1)):
        gram = s[i : i + 2]
        if gram not in out:
            out.append(gram)
    return out[:max_terms]


def _like_or_clause(query: str, column: str) -> tuple[str, list[str]]:
    grams = _bigrams(query)
    if not grams:
        return "", []
    clauses = " OR ".join(f"{column} LIKE ?" for _ in grams)
    return clauses, [f"%{g}%" for g in grams]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    def _c(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("database is not connected")
        return self.conn

    async def execute(self, sql: str, args: Iterable[Any] = ()) -> aiosqlite.Cursor:
        cur = await self._c().execute(sql, tuple(args))
        await self._c().commit()
        return cur

    async def fetchone(self, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
        cur = await self._c().execute(sql, tuple(args))
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = await self._c().execute(sql, tuple(args))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------------- messages ----------------

    async def message_exists(self, user_id: int, source_id: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 AS x FROM messages WHERE user_id=? AND source_id=?",
            (int(user_id), source_id),
        )
        return row is not None

    async def add_message(
        self,
        user_id: int,
        role: str,
        text: str,
        *,
        channel: str = "text",
        source_id: str | None = None,
        ts: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        c = self._c()
        ts = time.time() if ts is None else float(ts)
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        if source_id:
            cur = await c.execute(
                "INSERT OR IGNORE INTO messages(user_id,ts,role,text,channel,source_id,meta) "
                "VALUES(?,?,?,?,?,?,?)",
                (int(user_id), ts, role, text, channel, source_id, meta_json),
            )
            await c.commit()
            if cur.rowcount == 0:
                row = await self.fetchone(
                    "SELECT id FROM messages WHERE user_id=? AND source_id=?",
                    (int(user_id), source_id),
                )
                return int(row["id"]) if row else 0
            return int(cur.lastrowid or 0)

        cur = await c.execute(
            "INSERT INTO messages(user_id,ts,role,text,channel,meta) VALUES(?,?,?,?,?,?)",
            (int(user_id), ts, role, text, channel, meta_json),
        )
        await c.commit()
        return int(cur.lastrowid or 0)

    async def update_message_meta(
        self, message_id: int, meta: dict[str, Any]
    ) -> None:
        await self.execute(
            "UPDATE messages SET meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), int(message_id)),
        )

    async def recent_messages(
        self, user_id: int, limit: int = 30, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        if before_id is None:
            rows = await self.fetchall(
                "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), max(1, limit)),
            )
        else:
            rows = await self.fetchall(
                "SELECT * FROM messages WHERE user_id=? AND id < ? "
                "ORDER BY id DESC LIMIT ?",
                (int(user_id), before_id, max(1, limit)),
            )
        return list(reversed(rows))

    async def messages_since(
        self, user_id: int, since_ts: float, limit: int = 40
    ) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM messages WHERE user_id=? AND ts >= ? "
            "ORDER BY id DESC LIMIT ?",
            (int(user_id), float(since_ts), max(1, limit)),
        )
        return list(reversed(rows))

    async def count_messages(self, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM messages WHERE user_id=?", (int(user_id),)
        )
        return int(row["n"]) if row else 0

    async def count_all_messages(self) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS n FROM messages")
        return int(row["n"]) if row else 0

    async def search_messages(
        self, user_id: int, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        limit = max(1, limit)
        fts_query = _fts_or_query(q)
        if fts_query is None:
            return await self.fetchall(
                "SELECT * FROM messages WHERE user_id=? AND text LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (int(user_id), f"%{q}%", limit),
            )
        rows: list[dict[str, Any]] = []
        try:
            rows = await self.fetchall(
                "SELECT m.* FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? AND m.user_id=? LIMIT ?",
                (fts_query, int(user_id), min(200, limit * 8)),
            )
        except Exception:
            rows = []
        if not rows:
            clause, args = _like_or_clause(q, "text")
            if clause:
                rows = await self.fetchall(
                    f"SELECT * FROM messages WHERE user_id=? AND ({clause}) "
                    "ORDER BY id DESC LIMIT ?",
                    (int(user_id), *args, min(200, limit * 8)),
                )
        rows.sort(
            key=lambda row: (
                _overlap(q, row.get("text") or ""),
                float(row.get("ts") or 0),
            ),
            reverse=True,
        )
        return rows[:limit]

    async def delete_message(self, user_id: int, message_id: int) -> int:
        cursor = await self.execute(
            "DELETE FROM messages WHERE id=? AND user_id=?",
            (int(message_id), int(user_id)),
        )
        return int(cursor.rowcount or 0)

    # ---------------- facts ----------------

    async def add_fact(
        self,
        user_id: int,
        kind: str,
        content: str,
        *,
        topic: str = "",
        importance: int = 3,
        source_message_id: int | None = None,
        ts: float | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO facts(user_id,ts,kind,topic,content,importance,source_message_id,active) "
            "VALUES(?,?,?,?,?,?,?,1)",
            (
                int(user_id),
                time.time() if ts is None else float(ts),
                kind,
                topic,
                content,
                importance,
                source_message_id,
            ),
        )
        return int(cur.lastrowid or 0)

    async def list_facts(
        self, user_id: int, limit: int = 50, include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        active_clause = "" if include_inactive else "AND active=1 "
        return await self.fetchall(
            f"SELECT * FROM facts WHERE user_id=? {active_clause}"
            "ORDER BY importance DESC, ts DESC LIMIT ?",
            (int(user_id), max(1, limit)),
        )

    async def update_fact(
        self,
        user_id: int,
        fact_id: int,
        *,
        kind: str | None = None,
        topic: str | None = None,
        content: str | None = None,
        importance: int | None = None,
        active: bool | int | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if kind is not None:
            updates["kind"] = str(kind)
        if topic is not None:
            updates["topic"] = str(topic)
        if content is not None:
            updates["content"] = str(content)
        if importance is not None:
            updates["importance"] = int(importance)
        if active is not None:
            updates["active"] = 1 if active else 0
        if updates:
            assignments = ",".join(f"{key}=?" for key in updates)
            await self.execute(
                f"UPDATE facts SET {assignments} WHERE id=? AND user_id=?",
                (*updates.values(), int(fact_id), int(user_id)),
            )
        return await self.fetchone(
            "SELECT * FROM facts WHERE id=? AND user_id=?",
            (int(fact_id), int(user_id)),
        )

    async def delete_fact(self, user_id: int, fact_id: int) -> int:
        cursor = await self.execute(
            "DELETE FROM facts WHERE id=? AND user_id=?",
            (int(fact_id), int(user_id)),
        )
        return int(cursor.rowcount or 0)

    async def search_facts(
        self, user_id: int, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        limit = max(1, limit)
        fts_query = _fts_or_query(q)
        if fts_query is None:
            return await self.fetchall(
                "SELECT * FROM facts WHERE user_id=? AND active=1 AND content LIKE ? "
                "ORDER BY importance DESC, ts DESC LIMIT ?",
                (int(user_id), f"%{q}%", limit),
            )
        rows: list[dict[str, Any]] = []
        try:
            rows = await self.fetchall(
                "SELECT f.* FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                "WHERE facts_fts MATCH ? AND f.user_id=? AND f.active=1 LIMIT ?",
                (fts_query, int(user_id), min(200, limit * 8)),
            )
        except Exception:
            rows = []
        if not rows:
            clause, args = _like_or_clause(q, "content")
            if clause:
                rows = await self.fetchall(
                    f"SELECT * FROM facts WHERE user_id=? AND active=1 AND ({clause}) "
                    "ORDER BY importance DESC, ts DESC LIMIT ?",
                    (int(user_id), *args, min(200, limit * 8)),
                )
        rows.sort(
            key=lambda row: (
                _overlap(q, row.get("content") or ""),
                int(row.get("importance") or 0),
                float(row.get("ts") or 0),
            ),
            reverse=True,
        )
        return rows[:limit]

    async def deactivate_facts(self, user_id: int, query: str) -> int:
        q = (query or "").strip()
        if not q:
            return 0
        cur = await self.execute(
            "UPDATE facts SET active=0 WHERE user_id=? AND active=1 "
            "AND (content LIKE ? OR topic LIKE ?)",
            (int(user_id), f"%{q}%", f"%{q}%"),
        )
        return int(cur.rowcount or 0)

    # ---------------- topics ----------------

    async def upsert_topic(
        self,
        user_id: int,
        name: str,
        *,
        status: str = "open",
        importance: int = 3,
        last_mentioned: float | None = None,
        next_check_at: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO topics(user_id,name,status,importance,last_mentioned,next_check_at,meta) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,name) DO UPDATE SET "
            "status=excluded.status, importance=excluded.importance, "
            "last_mentioned=COALESCE(excluded.last_mentioned, topics.last_mentioned), "
            "next_check_at=excluded.next_check_at, meta=excluded.meta",
            (
                int(user_id),
                name,
                status,
                importance,
                time.time() if last_mentioned is None else float(last_mentioned),
                next_check_at,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )

    async def get_topic(self, user_id: int, name: str) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM topics WHERE user_id=? AND name=?", (int(user_id), name)
        )

    async def list_topics(
        self, user_id: int, statuses: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        if statuses is None:
            return await self.fetchall(
                "SELECT * FROM topics WHERE user_id=? "
                "ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END, "
                "importance DESC, last_mentioned DESC",
                (int(user_id),),
            )
        values = tuple(statuses)
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return await self.fetchall(
            f"SELECT * FROM topics WHERE user_id=? AND status IN ({placeholders}) "
            "ORDER BY importance DESC, last_mentioned DESC",
            (int(user_id), *values),
        )

    async def close_topics_matching(self, user_id: int, query: str) -> int:
        q = (query or "").strip()
        if not q:
            return 0
        cur = await self.execute(
            "UPDATE topics SET status='closed', next_check_at=NULL "
            "WHERE user_id=? AND name LIKE ?",
            (int(user_id), f"%{q}%"),
        )
        return int(cur.rowcount or 0)

    async def delete_topic(self, user_id: int, name: str) -> int:
        cursor = await self.execute(
            "DELETE FROM topics WHERE user_id=? AND name=?",
            (int(user_id), name),
        )
        return int(cursor.rowcount or 0)

    # ---------------- tags ----------------

    async def upsert_tag(
        self,
        user_id: int,
        tag: str,
        value: str,
        *,
        confidence: float = 0.5,
        evidence: str = "",
        source: str = "model",
    ) -> None:
        await self.execute(
            "INSERT INTO tags(user_id,tag,value,confidence,evidence,source,updated_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,tag) DO UPDATE SET value=excluded.value, "
            "confidence=excluded.confidence, evidence=excluded.evidence, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (
                int(user_id),
                tag,
                value,
                confidence,
                evidence,
                source,
                time.time(),
            ),
        )

    async def list_tags(self, user_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM tags WHERE user_id=? ORDER BY confidence DESC, updated_at DESC",
            (int(user_id),),
        )

    async def delete_tag(self, user_id: int, tag: str) -> int:
        cursor = await self.execute(
            "DELETE FROM tags WHERE user_id=? AND tag=?",
            (int(user_id), tag),
        )
        return int(cursor.rowcount or 0)

    # ---------------- state / stats ----------------

    async def set_state(self, user_id: int, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO state(user_id,key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (int(user_id), key, json.dumps(value, ensure_ascii=False), time.time()),
        )

    async def get_state(
        self, user_id: int, key: str, default: Any = None
    ) -> Any:
        row = await self.fetchone(
            "SELECT value FROM state WHERE user_id=? AND key=?", (int(user_id), key)
        )
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    async def set_stat(self, user_id: int, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO stats(user_id,key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (int(user_id), key, json.dumps(value, ensure_ascii=False), time.time()),
        )

    async def get_stat(
        self, user_id: int, key: str, default: Any = None
    ) -> Any:
        row = await self.fetchone(
            "SELECT value FROM stats WHERE user_id=? AND key=?", (int(user_id), key)
        )
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    # ---------------- scheduled thoughts ----------------

    async def schedule_thought(
        self,
        user_id: int,
        due_at: float,
        reason: str,
        *,
        topic: str = "",
    ) -> int:
        now = time.time()
        cur = await self.execute(
            "INSERT INTO scheduled_thoughts(user_id,due_at,reason,topic,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (int(user_id), float(due_at), reason.strip() or "主动想想", topic, "pending", now, now),
        )
        return int(cur.lastrowid or 0)

    async def upsert_thought_for_topic(
        self,
        user_id: int,
        due_at: float,
        reason: str,
        topic: str,
    ) -> int:
        """Avoid duplicate pending checks for the same topic."""
        if topic:
            row = await self.fetchone(
                "SELECT id FROM scheduled_thoughts WHERE user_id=? AND status='pending' "
                "AND topic=? ORDER BY id DESC LIMIT 1",
                (int(user_id), topic),
            )
            if row is not None:
                await self.execute(
                    "UPDATE scheduled_thoughts SET due_at=?, reason=?, updated_at=? WHERE id=?",
                    (float(due_at), reason.strip() or "主动想想", time.time(), int(row["id"])),
                )
                return int(row["id"])
        return await self.schedule_thought(
            user_id, due_at, reason, topic=topic
        )

    async def due_thoughts(
        self, user_id: int, now: float, limit: int = 5
    ) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM scheduled_thoughts WHERE user_id=? AND status='pending' "
            "AND due_at <= ? ORDER BY due_at ASC LIMIT ?",
            (int(user_id), float(now), max(1, limit)),
        )

    async def list_pending_thoughts(
        self, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM scheduled_thoughts WHERE user_id=? AND status='pending' "
            "ORDER BY due_at ASC LIMIT ?",
            (int(user_id), max(1, limit)),
        )

    async def mark_thought(self, thought_id: int, status: str) -> None:
        await self.execute(
            "UPDATE scheduled_thoughts SET status=?, updated_at=? WHERE id=?",
            (status, time.time(), int(thought_id)),
        )

    async def list_thoughts(
        self, user_id: int, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM scheduled_thoughts WHERE user_id=? "
            "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'processing' THEN 1 ELSE 2 END, "
            "due_at ASC LIMIT ?",
            (int(user_id), max(1, limit)),
        )

    async def update_thought(
        self,
        user_id: int,
        thought_id: int,
        *,
        due_at: float | None = None,
        reason: str | None = None,
        topic: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if due_at is not None:
            updates["due_at"] = float(due_at)
        if reason is not None:
            updates["reason"] = str(reason)
        if topic is not None:
            updates["topic"] = str(topic)
        if status is not None:
            updates["status"] = str(status)
        if updates:
            updates["updated_at"] = time.time()
            assignments = ",".join(f"{key}=?" for key in updates)
            await self.execute(
                f"UPDATE scheduled_thoughts SET {assignments} WHERE id=? AND user_id=?",
                (*updates.values(), int(thought_id), int(user_id)),
            )
        return await self.fetchone(
            "SELECT * FROM scheduled_thoughts WHERE id=? AND user_id=?",
            (int(thought_id), int(user_id)),
        )

    async def delete_thought(self, user_id: int, thought_id: int) -> int:
        cursor = await self.execute(
            "DELETE FROM scheduled_thoughts WHERE id=? AND user_id=?",
            (int(thought_id), int(user_id)),
        )
        return int(cursor.rowcount or 0)

    async def count_pending_thoughts(self, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM scheduled_thoughts WHERE user_id=? AND status='pending'",
            (int(user_id),),
        )
        return int(row["n"]) if row else 0

    async def count_all_pending_thoughts(self) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM scheduled_thoughts WHERE status='pending'"
        )
        return int(row["n"]) if row else 0

    async def cancel_thoughts_for_topic(self, user_id: int, topic: str) -> int:
        cur = await self.execute(
            "UPDATE scheduled_thoughts SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND status='pending' AND topic=?",
            (time.time(), int(user_id), topic),
        )
        return int(cur.rowcount or 0)

    async def cancel_thoughts_matching(self, user_id: int, query: str) -> int:
        q = (query or "").strip()
        if not q:
            return 0
        cur = await self.execute(
            "UPDATE scheduled_thoughts SET status='cancelled', updated_at=? "
            "WHERE user_id=? AND status='pending' AND (topic LIKE ? OR reason LIKE ?)",
            (time.time(), int(user_id), f"%{q}%", f"%{q}%"),
        )
        return int(cur.rowcount or 0)

    async def reset_processing_thoughts(self) -> None:
        await self.execute(
            "UPDATE scheduled_thoughts SET status='pending', updated_at=? WHERE status='processing'",
            (time.time(),),
        )

    # ---------------- decisions ----------------

    async def log_decision(
        self,
        user_id: int,
        trigger: str,
        summary: str,
        decision: dict[str, Any] | list[Any] | str,
        *,
        model: str = "",
    ) -> None:
        payload = decision if isinstance(decision, str) else json.dumps(decision, ensure_ascii=False)
        await self.execute(
            "INSERT INTO decisions(user_id,ts,trigger,summary,decision,model) VALUES(?,?,?,?,?,?)",
            (int(user_id), time.time(), trigger, summary[:500], payload[:8000], model),
        )

    async def recent_decisions(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM decisions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (int(user_id), max(1, limit)),
        )
