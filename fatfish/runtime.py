from __future__ import annotations

import dataclasses
import logging
from typing import Any

from . import state, users, voices
from .asr import build_asr
from .config import Settings
from .context import ContextBuilder
from .db import Database
from .engine import Engine
from .llm import build_llm
from .onebot import OneBotAdapter
from .scheduler import Scheduler
from .tts import TTSService, build_tts


class Runtime:
    """Holds the long-lived services and swaps them on config changes."""

    def __init__(self) -> None:
        self.settings = Settings.load()
        self.db = Database(self.settings.db_path)
        self.context = ContextBuilder(self.db, self.settings)
        self.llm = build_llm(self.settings)
        self.vision_llm = self._build_vision_llm(self.llm)
        self.asr = build_asr(self.settings)
        self.tts = None
        self.active_voice: dict[str, Any] | None = None
        self._tts_cache: dict[int, TTSService | None] = {}
        self.engine = Engine(
            self.db,
            self.settings,
            self.llm,
            self.context,
            tts=None,
        )
        self.engine.vision_llm = self.vision_llm
        self.scheduler = Scheduler(self.engine, self.db, self.settings)
        self.onebot = OneBotAdapter(self.engine, self.settings, asr=self.asr)
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def _build_vision_llm(self, base_llm):
        if self.settings.vision_model == self.settings.deepseek_model:
            return base_llm
        vision_settings = dataclasses.replace(
            self.settings, deepseek_model=self.settings.vision_model
        )
        return build_llm(vision_settings)

    def _wire(self) -> None:
        self.engine.settings = self.settings
        self.engine.llm = self.llm
        self.engine.vision_llm = self.vision_llm
        self.engine.tts = self.tts
        self.engine.tts_for = self.tts_for
        self.engine.context.settings = self.settings
        self.onebot.settings = self.settings
        self.onebot.asr = self.asr
        self.scheduler.settings = self.settings

    async def tts_for(self, user_id: int) -> TTSService | None:
        user_id = int(user_id)
        if user_id in self._tts_cache:
            return self._tts_cache[user_id]
        profile = await voices.get_active(self.db, user_id)
        if profile is None:
            profile = await voices.ensure_default_profile(
                self.db, self.settings, user_id
            )
        service = build_tts(self.settings, profile)
        self._tts_cache[user_id] = service
        return service

    async def ensure_user(self, user_id: int) -> None:
        user_id = int(user_id)
        await state.ensure_ready(self.db, user_id)
        await voices.ensure_default_profile(self.db, self.settings, user_id)
        self._tts_cache.pop(user_id, None)
        await self.tts_for(user_id)

    async def start(self) -> None:
        if self.db.conn is None:
            await self.db.connect()
        owner = await users.ensure_owner(self.db, self.settings)
        for user in await users.active_users(self.db):
            await self.ensure_user(int(user["id"]))
        if owner is not None:
            self.active_voice = await voices.get_active(self.db, int(owner["id"]))
            self.tts = build_tts(self.settings, self.active_voice)
        self._wire()
        if self.settings.proactive_enabled and not self.scheduler.is_running:
            await self.scheduler.start()
        self._started = True

    async def apply_settings(self, new_settings: Settings) -> None:
        self.settings = new_settings
        logging.getLogger().setLevel(
            getattr(logging, new_settings.log_level, logging.INFO)
        )
        if self.db.conn is not None:
            await users.ensure_owner(self.db, new_settings)
        self.llm = build_llm(new_settings)
        self.vision_llm = self._build_vision_llm(self.llm)
        self.asr = build_asr(new_settings)
        await self.reload_voice()
        self._wire()
        if new_settings.proactive_enabled and not self.scheduler.is_running:
            await self.scheduler.start()
        elif not new_settings.proactive_enabled and self.scheduler.is_running:
            await self.scheduler.stop()

    async def reload_voice(self, user_id: int | None = None) -> None:
        if user_id is None:
            self._tts_cache.clear()
            for user in await users.active_users(self.db):
                await self.ensure_user(int(user["id"]))
        else:
            await self.ensure_user(int(user_id))
        owner = await users.owner(self.db)
        if owner is not None:
            self.active_voice = await voices.get_active(
                self.db, int(owner["id"])
            )
            self.tts = build_tts(self.settings, self.active_voice)
        self._wire()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.db.close()
        self._started = False


runtime = Runtime()
