from __future__ import annotations

import asyncio
import json
import time

from fatfish import state, users
from fatfish.config import Settings
from fatfish.context import ContextBuilder
from fatfish.db import Database
from fatfish.engine import Engine
from fatfish.llm import MockLLM
from fatfish.models import LLMResponse, LLMToolCall


class FakeTTS:
    def __init__(self, path):
        self.path = path
        self.calls = []

    async def synthesize(self, text, voice="siliconflow"):
        self.calls.append((text, voice))
        return self.path


def _make_engine(tmp_path):
    settings = Settings.for_test(tmp_path)
    db = Database(settings.db_path)
    return settings, db


async def _make_user(db: Database) -> int:
    user = await users.create_user(db, "10001")
    user_id = int(user["id"])
    await state.ensure_ready(db, user_id)
    return user_id


def test_mock_turn_sends_voice_and_updates_world(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            engine = Engine(db, settings, MockLLM(), ContextBuilder(db, settings))
            result = await engine.handle_user_message(
                user_id, "明天上午要面试，有点紧张"
            )

            assert [item.kind for item in result.outgoing] == ["text", "voice"]
            assert result.outgoing[1].voice == "siliconflow"
            assert any(call.name == "open_topic" for call in result.tool_calls)
            assert result.user_id == user_id

            topics = await db.list_topics(user_id)
            assert topics and topics[0]["name"] == "面试"
            assert topics[0]["next_check_at"] > time.time()

            facts = await db.list_facts(user_id)
            assert facts and "面试" in facts[0]["content"]

            thoughts = await db.list_pending_thoughts(user_id)
            assert thoughts and thoughts[0]["topic"] == "面试"

            mood, rel = await state.load_state(db, user_id)
            assert mood["worry"] > 5
            assert rel["trust"] > state.default_relationship()["trust"]

            decisions = await db.recent_decisions(user_id, 5)
            assert decisions and decisions[0]["trigger"] == "user"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_duplicate_source_id_is_ignored(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            engine = Engine(db, settings, MockLLM(), ContextBuilder(db, settings))
            first = await engine.handle_user_message(
                user_id, "你好", source_id="same-id"
            )
            second = await engine.handle_user_message(
                user_id, "你好", source_id="same-id"
            )
            assert first.duplicate is False
            assert second.duplicate is True
            assert await db.count_messages(user_id) == 2
        finally:
            await db.close()

    asyncio.run(scenario())


def test_engine_synthesizes_voice_and_updates_meta(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            script = [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        LLMToolCall(
                            id="v1",
                            name="send_voice",
                            arguments={"text": "你好呀", "voice": "siliconflow"},
                        )
                    ],
                )
            ]
            audio = tmp_path / "fake.wav"
            tts = FakeTTS(audio)
            engine = Engine(
                db,
                settings,
                MockLLM(script=script),
                ContextBuilder(db, settings),
                tts=tts,
            )
            await engine.handle_user_message(user_id, "说句话")
            assert tts.calls == [("你好呀", "siliconflow")]

            rows = await db.recent_messages(user_id, 10)
            assistant = [row for row in rows if row["role"] == "assistant"][0]
            assert json.loads(assistant["meta"])["audio_path"] == str(audio)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_engine_prefers_per_user_tts(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            script = [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        LLMToolCall(
                            id="v1",
                            name="send_voice",
                            arguments={"text": "用我的音色", "voice": "siliconflow"},
                        )
                    ],
                )
            ]
            default_tts = FakeTTS(tmp_path / "default.wav")
            user_tts = FakeTTS(tmp_path / "user.wav")
            engine = Engine(
                db,
                settings,
                MockLLM(script=script),
                ContextBuilder(db, settings),
                tts=default_tts,
            )
            seen = []

            async def tts_for(requested):
                seen.append(requested)
                return user_tts

            engine.tts_for = tts_for
            await engine.handle_user_message(user_id, "说句话")
            assert seen == [user_id]
            assert user_tts.calls == [("用我的音色", "siliconflow")]
            assert default_tts.calls == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_voice_marker_in_plain_text_is_sent_as_voice(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            script = [LLMResponse(content="[语音：我在呢，刚回宿舍]")]
            audio = tmp_path / "marker.wav"
            tts = FakeTTS(audio)
            engine = Engine(
                db,
                settings,
                MockLLM(script=script),
                ContextBuilder(db, settings),
                tts=tts,
            )
            result = await engine.handle_user_message(user_id, "发个语音听听")
            assert [item.kind for item in result.outgoing] == ["voice"]
            assert result.outgoing[0].text == "我在呢，刚回宿舍"
            assert tts.calls == [("我在呢，刚回宿舍", "siliconflow")]

            rows = await db.recent_messages(user_id, 5)
            assistant = [row for row in rows if row["role"] == "assistant"][0]
            assert assistant["channel"] == "voice"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_voice_probability_converts_text_to_voice(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        settings.voice_probability = 1.0
        await db.connect()
        try:
            user_id = await _make_user(db)
            script = [LLMResponse(content="这句话本来是按文字发的")]
            audio = tmp_path / "probability.wav"
            tts = FakeTTS(audio)
            engine = Engine(
                db,
                settings,
                MockLLM(script=script),
                ContextBuilder(db, settings),
                tts=tts,
            )
            result = await engine.handle_user_message(user_id, "随便聊聊")
            assert [item.kind for item in result.outgoing] == ["voice"]
            assert tts.calls == [("这句话本来是按文字发的", "siliconflow")]
            rows = await db.recent_messages(user_id, 5)
            assistant = [row for row in rows if row["role"] == "assistant"][0]
            assert assistant["channel"] == "voice"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_proactive_can_choose_no_op(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            engine = Engine(db, settings, MockLLM(), ContextBuilder(db, settings))
            thought_id = await db.schedule_thought(
                user_id, time.time() - 1, "看看他在干嘛"
            )
            thought = (await db.due_thoughts(user_id, time.time()))[0]

            result = await engine.handle_proactive(user_id, thought)
            assert result.outgoing == []
            assert any(call.name == "no_op" for call in result.tool_calls)
            assert result.user_id == user_id
            assert await db.list_pending_thoughts(user_id)

            decisions = await db.recent_decisions(user_id, 5)
            assert decisions and decisions[0]["trigger"] == "proactive"
            assert thought_id > 0
        finally:
            await db.close()

    asyncio.run(scenario())


class CaptureLLM:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(
            content=None,
            tool_calls=[
                LLMToolCall(id="m1", name="send_text", arguments={"text": "看到了"})
            ],
        )


class JsonLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self.content)


def test_thought_precheck_without_new_messages_fires(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            llm = JsonLLM('{"action":"cancel","reason":"不应该被调用"}')
            engine = Engine(db, settings, llm, ContextBuilder(db, settings))
            check = await engine.precheck_thought(
                user_id,
                {
                    "created_at": time.time() - 600,
                    "reason": "提醒他关灯",
                    "topic": "",
                },
            )
            assert check["action"] == "fire"
            assert llm.calls == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_thought_precheck_can_revise_stale_topic(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            await db.add_message(
                user_id, "user", "下午组会开完了，还挺顺利", ts=time.time()
            )
            llm = JsonLLM(
                '{"action":"revise","reason":"组会已经聊过",'
                '"new_reason":"只提醒他关灯","new_topic":""}'
            )
            engine = Engine(db, settings, llm, ContextBuilder(db, settings))
            check = await engine.precheck_thought(
                user_id,
                {
                    "created_at": time.time() - 3600,
                    "reason": "提醒他关灯，顺便问问组会",
                    "topic": "组会",
                },
            )
            assert check["action"] == "revise"
            assert check["new_reason"] == "只提醒他关灯"
            assert llm.calls and "下午组会开完了" in llm.calls[0][-1]["content"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_thought_precheck_can_cancel(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            await db.add_message(user_id, "user", "我已经睡了", ts=time.time())
            llm = JsonLLM(
                '{"action":"cancel","reason":"他已经道晚安了","new_reason":"","new_topic":""}'
            )
            engine = Engine(db, settings, llm, ContextBuilder(db, settings))
            check = await engine.precheck_thought(
                user_id,
                {
                    "created_at": time.time() - 3600,
                    "reason": "看看他睡了没",
                    "topic": "",
                },
            )
            assert check["action"] == "cancel"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_engine_stores_media_and_sends_image_parts(tmp_path):
    async def scenario():
        settings, db = _make_engine(tmp_path)
        await db.connect()
        try:
            user_id = await _make_user(db)
            media_dir = tmp_path / "media" / "images"
            media_dir.mkdir(parents=True)
            (media_dir / "pic.jpg").write_bytes(b"fake-jpeg")
            llm = CaptureLLM()
            engine = Engine(db, settings, llm, ContextBuilder(db, settings))

            await engine.handle_user_message(
                user_id,
                "[图片]",
                source_id="img-1",
                media=[{"kind": "image", "path": "media/images/pic.jpg"}],
            )

            rows = await db.recent_messages(user_id, 10)
            user = [row for row in rows if row["role"] == "user"][0]
            meta = json.loads(user["meta"])
            assert meta["media"][0]["path"] == "media/images/pic.jpg"

            last_user = [m for m in llm.calls[0] if m["role"] == "user"][-1]
            assert isinstance(last_user["content"], list)
            assert any(part["type"] == "image_url" for part in last_user["content"])
        finally:
            await db.close()

    asyncio.run(scenario())
