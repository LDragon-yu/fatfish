from __future__ import annotations

import asyncio
from pathlib import Path

import fatfish.onebot as onebot_module
from fatfish import state, users
from fatfish.config import Settings
from fatfish.context import ContextBuilder
from fatfish.db import Database
from fatfish.engine import Engine
from fatfish.llm import MockLLM
from fatfish.onebot import (
    OneBotAdapter,
    build_record_message,
    build_rich_message,
    build_text_message,
    face_label,
    mface_label,
    parse_private_event,
    split_text_segments,
    typing_delay_for,
)


def _event(user_id=123, message_type="private"):
    return {
        "post_type": "message",
        "message_type": message_type,
        "user_id": user_id,
        "message_id": 456,
        "message": [
            {"type": "text", "data": {"text": "你好"}},
            {"type": "record", "data": {"file": "a.amr"}},
            {"type": "image", "data": {"url": "https://example.com/a.png"}},
            {"type": "video", "data": {"url": "https://example.com/a.mp4"}},
            {"type": "face", "data": {"id": "14"}},
            {
                "type": "mface",
                "data": {
                    "emoji_package_id": 1,
                    "emoji_id": "abc",
                    "key": "k",
                    "summary": "笑死",
                },
            },
        ],
    }


def test_parse_private_event():
    parsed = parse_private_event(_event())
    assert parsed is not None
    assert parsed["text"] == "你好"
    assert len(parsed["records"]) == 1
    assert len(parsed["images"]) == 1
    assert len(parsed["videos"]) == 1
    assert len(parsed["faces"]) == 1
    assert len(parsed["mfaces"]) == 1
    assert parsed["message_id"] == 456


def test_face_labels():
    assert face_label({"id": "14"}) == "[QQ表情:微笑]"
    assert face_label({"id": "14", "chainCount": 3}) == "[QQ表情:微笑×3]"
    assert face_label({"id": "999999"}) == "[QQ表情:999999]"
    assert mface_label({"summary": "笑死"}) == "[表情包:笑死]"
    assert mface_label({"summary": "[动画表情]"}) == "[表情包]"


def test_build_rich_message_converts_face_markers():
    parts = build_rich_message("好呀 [QQ表情:调皮]")
    assert parts[0] == {"type": "text", "data": {"text": "好呀"}}
    assert parts[1]["type"] == "face"
    assert parts[1]["data"]["id"] == "12"

    parts = build_rich_message("[QQ表情:微笑×3]")
    assert [p["type"] for p in parts] == ["face", "face", "face"]

    assert build_rich_message("[表情包:笑死]") == []
    assert build_rich_message("普通文字") == [
        {"type": "text", "data": {"text": "普通文字"}}
    ]
    assert build_rich_message("好[QQ表情:不存在的表情]") == [
        {"type": "text", "data": {"text": "好"}}
    ]


def test_send_text_converts_face_marker(tmp_path):
    async def scenario():
        settings = Settings.for_test(tmp_path)
        adapter = OneBotAdapter(None, settings, asr=None)  # type: ignore[arg-type]
        captured = []

        async def fake_call(action, params, timeout=30.0):
            captured.append(params)
            return {"retcode": 0, "status": "ok"}

        adapter._call_action = fake_call  # type: ignore[method-assign]
        await adapter._send_text(
            "好呀 [QQ表情:调皮]", {"platform_id": "10001"}
        )
        assert captured
        message = captured[0]["message"]
        assert message[0] == {"type": "text", "data": {"text": "好呀"}}
        assert message[1]["type"] == "face"
        assert captured[0]["user_id"] == 10001

    asyncio.run(scenario())


def test_typing_delay_for():
    assert typing_delay_for("你好呀") == 0.75
    assert typing_delay_for("好") == 0.6
    assert typing_delay_for("长" * 40) == 8.0
    assert typing_delay_for("好", floor=1.0) == 1.0
    assert typing_delay_for("  ") == 0.0


def test_incoming_face_reaches_engine_text(tmp_path, monkeypatch):
    monkeypatch.setattr(onebot_module, "INBOUND_DELAY_MIN", 0.01)
    monkeypatch.setattr(onebot_module, "INBOUND_DELAY_MAX", 0.02)

    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "20001")
            user_id = int(user["id"])
            await state.ensure_ready(db, user_id)
            engine = Engine(
                db, settings, MockLLM(), ContextBuilder(db, settings)
            )
            adapter = OneBotAdapter(engine, settings, asr=None)
            await adapter._handle_message(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 20001,
                    "message_id": 9100,
                    "message": [
                        {"type": "face", "data": {"id": "14"}},
                        {
                            "type": "mface",
                            "data": {"summary": "笑死"},
                        },
                    ],
                }
            )
            await asyncio.sleep(0.1)
            rows = await db.recent_messages(user_id, 10)
            user_text = [row["text"] for row in rows if row["role"] == "user"][0]
            assert "[QQ表情:微笑]" in user_text
            assert "[表情包:笑死]" in user_text
            assert any(row["role"] == "assistant" for row in rows)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_burst_messages_are_coalesced(tmp_path, monkeypatch):
    monkeypatch.setattr(onebot_module, "INBOUND_DELAY_MIN", 0.2)
    monkeypatch.setattr(onebot_module, "INBOUND_DELAY_MAX", 0.2)

    async def scenario():
        settings = Settings.for_test(tmp_path)
        db = Database(settings.db_path)
        await db.connect()
        try:
            user = await users.create_user(db, "20002")
            user_id = int(user["id"])
            await state.ensure_ready(db, user_id)
            engine = Engine(
                db, settings, MockLLM(), ContextBuilder(db, settings)
            )
            adapter = OneBotAdapter(engine, settings, asr=None)
            for message_id, text in ((9201, "第一条"), (9202, "第二条")):
                await adapter._handle_message(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "user_id": 20002,
                        "message_id": message_id,
                        "message": [
                            {"type": "text", "data": {"text": text}}
                        ],
                    }
                )
            await asyncio.sleep(0.6)
            rows = await db.recent_messages(user_id, 10)
            user_rows = [row for row in rows if row["role"] == "user"]
            assert len(user_rows) == 1
            assert "第一条" in user_rows[0]["text"]
            assert "第二条" in user_rows[0]["text"]
            assert any(row["role"] == "assistant" for row in rows)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_parse_rejects_group_and_accepts_any_private_user():
    assert parse_private_event(_event(message_type="group")) is None
    # 白名单/用户解析由 OneBotAdapter 负责，解析层不再按 owner 过滤
    assert parse_private_event(_event(user_id=999)) is not None


def test_build_messages():
    text_message = build_text_message("hi")
    assert text_message == [{"type": "text", "data": {"text": "hi"}}]

    record_message = build_record_message(Path("/tmp/a.wav"))
    assert record_message[0]["type"] == "record"
    assert record_message[0]["data"]["file"].startswith("file://")


def test_split_text_segments():
    assert split_text_segments("第一句\n\n第二句\n") == ["第一句", "第二句"]
    assert split_text_segments("  \n ") == []
    assert split_text_segments("  单行  ") == ["单行"]

    long = "\n".join(f"m{index}" for index in range(30))
    segments = split_text_segments(long)
    assert len(segments) == 20
    assert segments[0] == "m0"
    assert segments[-1].startswith("m19")
