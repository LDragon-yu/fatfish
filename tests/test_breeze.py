from __future__ import annotations

import asyncio

from fatfish import voices
from fatfish.config import Settings
from fatfish.tts import BreezeTTS, TTSService, build_tts


def _settings(tmp_path) -> Settings:
    settings = Settings.for_test(tmp_path)
    settings.breeze_api_key = "brz_test"
    settings.breeze_language_code = "zh"
    return settings


def test_breeze_profile_selects_breeze_provider(tmp_path):
    settings = _settings(tmp_path)
    profile = {
        "provider": "breeze",
        "name": "Breeze 测试音色",
        "model": "breeze-tts-2",
        "voice_uri": "voc_123",
        "speed": 1.0,
    }
    service = TTSService(settings, profile)
    assert service.provider == "breeze"
    assert service.available()
    assert service.voice_name == "Breeze 测试音色"

    settings.breeze_api_key = ""
    assert not TTSService(settings, profile).available()
    assert build_tts(settings, profile) is None


def test_breeze_tts_request_contract(tmp_path):
    settings = _settings(tmp_path)
    tts = BreezeTTS(
        settings,
        {
            "provider": "breeze",
            "model": "breeze-tts-2",
            "voice_uri": "voc_123",
            "speed": 0.8,
        },
    )
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"fake-wav"
        text = ""

    class FakeClient:
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    content = asyncio.run(tts._request(FakeClient(), "你好呀"))
    assert content == b"fake-wav"
    assert captured["url"] == "https://api.breeze.blue/v1/text-to-speech/voc_123"
    assert captured["headers"] == {"xi-api-key": "brz_test"}
    assert captured["params"] == {"output_format": "wav_24000"}
    assert captured["json"]["text"] == "你好呀"
    assert captured["json"]["model_id"] == "breeze-tts-2"
    assert captured["json"]["language_code"] == "zh"
    assert captured["json"]["voice_settings"] == {"speed": 0.8}


def test_breeze_clone_preview_and_save(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/v1/voice-previews/clone"):
                return FakeResponse({"generated_voice_id": "gen_123"})
            if url.endswith("/v1/voice-previews/gen_123/save"):
                return FakeResponse({"voice_id": "voc_saved"})
            raise AssertionError(url)

    monkeypatch.setattr(
        voices.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient()
    )
    voice_id = asyncio.run(
        voices.clone_breeze_voice(
            settings, b"audio-bytes", "sample.wav", "测试音色"
        )
    )
    assert voice_id == "voc_saved"
    clone_url, clone_kwargs = calls[0]
    assert clone_url == "https://api.breeze.blue/v1/voice-previews/clone"
    assert clone_kwargs["headers"] == {"xi-api-key": "brz_test"}
    assert clone_kwargs["data"]["reference_language_code"] == "zh"
    field, file_tuple = clone_kwargs["files"][0]
    assert field == "files"
    assert file_tuple[0] == "sample.wav"
    assert file_tuple[1] == b"audio-bytes"
    save_url, save_kwargs = calls[1]
    assert save_url.endswith("/v1/voice-previews/gen_123/save")
    assert save_kwargs["json"] == {
        "voice_name": "测试音色",
        "language_code": "zh",
    }
