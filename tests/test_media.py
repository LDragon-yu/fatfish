from __future__ import annotations

import asyncio

from fatfish.media import clean_description, describe_image, describe_video
from fatfish.models import LLMResponse


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self.content)


def test_clean_description():
    assert clean_description('  "一张面包的照片。" ') == "一张面包的照片。"
    assert clean_description("[图片] 他在吃早饭\n第二行") == "他在吃早饭"
    assert clean_description(None) == ""
    assert len(clean_description("长" * 300)) == 120


def test_describe_image_sends_image_part(tmp_path):
    async def scenario():
        image = tmp_path / "pic.jpg"
        image.write_bytes(b"fake-jpeg")
        llm = FakeLLM("一个人抱着一袋面包。")
        text = await describe_image(llm, image)
        assert text == "一个人抱着一袋面包。"
        content = llm.calls[0][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    asyncio.run(scenario())


def test_describe_video_uses_frames_and_transcript(tmp_path):
    async def scenario():
        frames = []
        for index in range(8):
            frame = tmp_path / f"f{index}.jpg"
            frame.write_bytes(b"fake-frame")
            frames.append(frame)
        llm = FakeLLM("他在直播间里说话。")
        text = await describe_video(llm, frames, "大家好，欢迎来到直播间", 21.6)
        assert text == "他在直播间里说话。"
        content = llm.calls[0][0]["content"]
        image_parts = [part for part in content if part["type"] == "image_url"]
        assert len(image_parts) == 6
        assert "音轨转写：大家好" in content[0]["text"]

    asyncio.run(scenario())
