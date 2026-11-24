from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class ASRError(RuntimeError):
    pass


class SiliconFlowASR:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.siliconflow_api_key
        self.base_url = settings.siliconflow_base_url
        self.model = settings.siliconflow_asr_model

    def available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(self, path: Path) -> str:
        if not self.available():
            raise ASRError("没有 SILICONFLOW_API_KEY")
        path = Path(path)
        if not path.exists():
            raise ASRError(f"音频文件不存在：{path}")

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        logger.info("ASR 识别：%s（%.1f KiB）", path.name, path.stat().st_size / 1024)
        try:
            with path.open("rb") as handle:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    response = await client.post(
                        f"{self.base_url}/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data={"model": self.model},
                        files={"file": (path.name, handle, mime)},
                    )
        except httpx.HTTPError as exc:
            raise ASRError(f"ASR 请求失败：{type(exc).__name__}: {exc}") from exc

        if response.status_code >= 400:
            raise ASRError(
                f"ASR HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ASRError(f"ASR 返回不是 JSON：{response.text[:300]}") from exc

        text = str(payload.get("text") or "")
        text = re.sub(r"<\|[^|>]*\|>", "", text).strip()
        return text


def build_asr(settings: Settings) -> SiliconFlowASR | None:
    service = SiliconFlowASR(settings)
    return service if service.available() else None
