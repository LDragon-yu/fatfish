from __future__ import annotations

import asyncio
import logging
import time

from . import users
from .config import Settings
from .db import Database
from .engine import Engine

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, engine: Engine, db: Database, settings: Settings):
        self.engine = engine
        self.db = db
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        await self.db.reset_processing_thoughts()
        await self._seed_if_needed()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="fatfish-scheduler")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def _seed_if_needed(self) -> None:
        """Give every active user a first chance to be proactive after a restart."""
        if not self.settings.proactive_enabled:
            return
        for user in await users.active_users(self.db):
            user_id = int(user["id"])
            if await self.db.count_pending_thoughts(user_id) > 0:
                continue
            if await self.db.count_messages(user_id) <= 0:
                continue
            await self.db.schedule_thought(
                user_id,
                time.time() + 30 * 60,
                "看看要不要主动找他",
            )
            logger.info("已为用户 %s 播种一条 30 分钟后的主动思考", user_id)

    async def run_once(self) -> int:
        if not self.settings.proactive_enabled:
            return 0
        if self.engine.is_busy:
            return 0

        processed = 0
        for user in await users.active_users(self.db):
            user_id = int(user["id"])
            due = await self.db.due_thoughts(user_id, time.time())
            for thought in due:
                precheck = getattr(self.engine, "precheck_thought", None)
                if precheck is not None:
                    check = await precheck(user_id, thought)
                    action = str(check.get("action") or "fire")
                    if action == "cancel":
                        await self.db.mark_thought(thought["id"], "cancelled")
                        logger.info(
                            "主动思考预检取消 user=%s thought=%s reason=%s",
                            user_id,
                            thought["id"],
                            check.get("reason") or "",
                        )
                        continue
                    if action == "revise":
                        new_reason = str(check.get("new_reason") or "").strip()
                        if new_reason:
                            new_topic = str(check.get("new_topic") or "").strip()
                            await self.db.update_thought(
                                user_id,
                                thought["id"],
                                reason=new_reason,
                                topic=new_topic,
                            )
                            thought = {
                                **thought,
                                "reason": new_reason,
                                "topic": new_topic,
                            }
                            logger.info(
                                "主动思考预检改写 user=%s thought=%s -> %s",
                                user_id,
                                thought["id"],
                                new_reason,
                            )
                await self.db.mark_thought(thought["id"], "processing")
                try:
                    await self.engine.handle_proactive(user_id, thought)
                    await self.db.mark_thought(thought["id"], "done")
                    processed += 1
                except Exception:  # noqa: BLE001 - keep the thought for retry
                    logger.exception("主动思考处理失败，恢复为 pending")
                    await self.db.mark_thought(thought["id"], "pending")
        return processed

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - scheduler must keep running
                logger.exception("调度器循环异常")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(1, self.settings.scheduler_interval_seconds),
                )
            except asyncio.TimeoutError:
                pass
