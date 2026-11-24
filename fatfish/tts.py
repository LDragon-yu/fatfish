from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import shutil
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*")
COMMA_RE = re.compile(r"(?<=[，,、])\s*")


class TTSError(RuntimeError):
    pass


def split_for_tts(text: str, max_chars: int = 120, total_max: int = 300) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) > total_max:
        raise TTSError(f"语音文本太长（{len(text)} 字，上限 {total_max}）")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    for sentence in SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        buf = ""
        for piece in COMMA_RE.split(sentence):
            piece = piece.strip()
            if not piece:
                continue
            while len(piece) > max_chars:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(piece[:max_chars])
                piece = piece[max_chars:]
            if len(buf) + len(piece) <= max_chars:
                buf += piece
            else:
                if buf:
                    chunks.append(buf)
                buf = piece
        if buf:
            chunks.append(buf)

    if len(chunks) > 12:
        raise TTSError(f"语音文本拆成 {len(chunks)} 段，过多")
    return chunks


class _TTSBase:
    settings: Settings
    ffmpeg: str

    async def _run_ffmpeg(self, args: list[str]) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg,
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise TTSError(f"找不到 ffmpeg：{self.ffmpeg}") from exc
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise TTSError(f"ffmpeg 失败：{stderr.decode(errors='ignore')[:300]}")

    async def _normalize(self, raw: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._run_ffmpeg(
                [
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(raw),
                    "-af",
                    "loudnorm=I=-18:TP=-2:LRA=7",
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
            return output
        except Exception as exc:  # noqa: BLE001 - fall back to raw audio
            logger.warning("TTS 响度归一化失败，使用原始音频：%s", exc)
            shutil.copyfile(raw, output)
            return output

    def _concat_wavs(self, files: list[Path], output: Path) -> Path:
        """Concatenate same-format PCM wavs with 150ms gaps, no ffmpeg needed."""
        with wave.open(str(files[0]), "rb") as first:
            params = first.getparams()
        silence = b"\x00" * int(
            params.framerate * 0.15 * params.nchannels * params.sampwidth
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as out:
            out.setparams(params)
            for index, path in enumerate(files):
                with wave.open(str(path), "rb") as handle:
                    if (
                        handle.getnchannels() != params.nchannels
                        or handle.getsampwidth() != params.sampwidth
                        or handle.getframerate() != params.framerate
                    ):
                        raise ValueError("wav 参数不一致")
                    out.writeframes(handle.readframes(handle.getnframes()))
                if index < len(files) - 1:
                    out.writeframes(silence)
        return output


class SiliconFlowTTS(_TTSBase):
    """CosyVoice2 clone voice, configured by an active voice profile."""

    def __init__(self, settings: Settings, profile: dict | None = None):
        self.settings = settings
        self.ffmpeg = settings.ffmpeg_path
        self.api_key = settings.siliconflow_api_key
        self.base_url = settings.siliconflow_base_url
        profile = profile or {}
        self.model = str(profile.get("model") or settings.siliconflow_tts_model)
        self.mode = str(profile.get("mode") or settings.siliconflow_tts_mode)
        try:
            self.speed = float(profile.get("speed") or settings.siliconflow_tts_speed)
        except (TypeError, ValueError):
            self.speed = settings.siliconflow_tts_speed
        reference = str(profile.get("reference_path") or "").strip()
        self.reference = (
            Path(reference).expanduser()
            if reference
            else settings.siliconflow_tts_reference
        )
        self.reference_text_path = settings.siliconflow_tts_reference_text
        self.voice_uri = str(profile.get("voice_uri") or "").strip()
        self._profile_text = str(profile.get("reference_text") or "").strip()
        self.output_dir = settings.data_dir / "voice" / "out" / "tts"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._reference_uri: str | None = None
        self._reference_text: str = ""
        self._lock = asyncio.Lock()
        if self.mode == "upload" and not self.voice_uri:
            self.voice_uri = self._load_voice_uri_file()

    def _load_voice_uri_file(self) -> str:
        try:
            payload = json.loads(
                self.settings.siliconflow_voice_file.read_text(encoding="utf-8")
            )
            return str(payload.get("uri") or "").strip()
        except (OSError, ValueError):
            return ""

    def available(self) -> bool:
        if not self.api_key:
            return False
        if self.mode == "upload":
            return bool(self.voice_uri)
        return True

    def _load_reference(self) -> None:
        if self._reference_uri is not None:
            return
        if not self.reference.exists():
            raise TTSError(f"参考音频不存在：{self.reference}")
        suffix = self.reference.suffix.lower()
        mime = "audio/mpeg" if suffix == ".mp3" else "audio/wav"
        raw = self.reference.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        self._reference_uri = f"data:{mime};base64,{encoded}"
        if self._profile_text:
            self._reference_text = self._profile_text
            return
        if not self.reference_text_path.exists():
            raise TTSError(f"参考文字稿不存在：{self.reference_text_path}")
        self._reference_text = "\n".join(
            line.strip()
            for line in self.reference_text_path.read_text(
                encoding="utf-8-sig"
            ).splitlines()
            if line.strip()
        )
        if not self._reference_text:
            raise TTSError("参考文字稿为空")

    async def _request(self, client: httpx.AsyncClient, text: str) -> bytes:
        payload: dict = {
            "model": self.model,
            "input": text,
            "response_format": "wav",
            "sample_rate": 24000,
            "speed": self.speed,
            "stream": False,
        }
        if self.mode == "upload" and self.voice_uri:
            payload["voice"] = self.voice_uri
        else:
            payload["voice"] = ""
            payload["references"] = [
                {"audio": self._reference_uri, "text": self._reference_text}
            ]
        response = await client.post(
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        if response.status_code >= 400:
            raise TTSError(
                f"SiliconFlow TTS HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            raise TTSError("SiliconFlow TTS 返回空音频")
        return response.content

    async def synthesize(self, text: str, output: Path | None = None) -> Path:
        if not self.available():
            raise TTSError("SiliconFlow 音色未配置或缺少 API key")
        if not (self.mode == "upload" and self.voice_uri):
            self._load_reference()
        chunks = split_for_tts(text, total_max=self.settings.tts_max_chars)
        if not chunks:
            raise TTSError("语音文本为空")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        token = uuid.uuid4().hex[:8]
        output = output or self.output_dir / f"tts_{stamp}_{token}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            async with httpx.AsyncClient(timeout=180.0) as client:
                raws: list[Path] = []
                normalized: list[Path] = []
                for index, chunk in enumerate(chunks):
                    logger.info(
                        "SiliconFlow TTS 合成 %s 段，第 %s 段 %s 字",
                        len(chunks),
                        index + 1,
                        len(chunk),
                    )
                    content = await self._request(client, chunk)
                    raw = output.with_name(f"{output.stem}_part{index}_raw.wav")
                    raw.write_bytes(content)
                    norm = await self._normalize(
                        raw, output.with_name(f"{output.stem}_part{index}.wav")
                    )
                    raws.append(raw)
                    normalized.append(norm)

            try:
                if len(normalized) == 1:
                    shutil.copyfile(normalized[0], output)
                else:
                    try:
                        self._concat_wavs(normalized, output)
                    except Exception as exc:  # noqa: BLE001 - fallback to ffmpeg
                        logger.warning("wav 拼接失败，改用 ffmpeg：%s", exc)
                        await self._concat_with_ffmpeg(normalized, output)
            finally:
                for path in raws + normalized:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        return output

    async def _concat_with_ffmpeg(self, files: list[Path], output: Path) -> None:
        silence = output.with_name(f"{output.stem}_silence.wav")
        await self._run_ffmpeg(
            [
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                "0.15",
                "-c:a",
                "pcm_s16le",
                str(silence),
            ]
        )
        list_file = output.with_name(f"{output.stem}_concat.txt")
        lines: list[str] = []
        for index, path in enumerate(files):
            lines.append(f"file '{path.as_posix()}'")
            if index < len(files) - 1:
                lines.append(f"file '{silence.as_posix()}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            await self._run_ffmpeg(
                [
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
        finally:
            for path in (silence, list_file):
                try:
                    path.unlink()
                except OSError:
                    pass


class MiMoTTS(_TTSBase):
    """MiMo preset voice fallback (茉莉)."""

    def __init__(self, settings: Settings, profile: dict | None = None):
        self.settings = settings
        self.ffmpeg = settings.ffmpeg_path
        self.api_key = settings.mimo_api_key
        self.base_url = settings.mimo_base_url
        self.model = settings.mimo_tts_model
        profile = profile or {}
        self.default_voice = str(
            profile.get("voice_name") or settings.mimo_tts_voice
        ).strip() or "茉莉"
        self.output_dir = settings.data_dir / "voice" / "out" / "tts"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return bool(self.api_key)

    async def synthesize_preset(
        self, text: str, voice: str | None = None, output: Path | None = None
    ) -> Path:
        if not self.available():
            raise TTSError("没有 MIMO_API_KEY")
        voice = (voice or self.default_voice or "茉莉").strip()
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise TTSError("缺少 openai 依赖") from exc
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=180)
        completion = await client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": "年轻女生，语气自然，带一点笑意，语速适中，像在 QQ 上发语音聊天",
                },
                {"role": "assistant", "content": text},
            ],
            audio={"format": "wav", "voice": voice},
        )
        message = completion.choices[0].message
        audio = getattr(message, "audio", None)
        if audio is None or not getattr(audio, "data", None):
            raise TTSError("MiMo TTS 没有返回音频")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        token = uuid.uuid4().hex[:8]
        output = output or self.output_dir / f"mimo_{stamp}_{token}.wav"
        raw = output.with_name(f"{output.stem}_raw.wav")
        raw.write_bytes(base64.b64decode(audio.data))
        try:
            return await self._normalize(raw, output)
        finally:
            try:
                raw.unlink()
            except OSError:
                pass


class BreezeTTS(_TTSBase):
    """Breeze Blue cloned voice (voice_id saved by the voice studio)."""

    def __init__(self, settings: Settings, profile: dict | None = None):
        self.settings = settings
        self.ffmpeg = settings.ffmpeg_path
        self.api_key = settings.breeze_api_key
        self.base_url = settings.breeze_base_url.rstrip("/")
        profile = profile or {}
        self.model = str(profile.get("model") or settings.breeze_tts_model)
        self.language_code = (
            str(profile.get("language_code") or settings.breeze_language_code)
            or "zh"
        )
        self.voice_id = str(profile.get("voice_uri") or "").strip()
        try:
            self.speed = float(profile.get("speed") or 1.0)
        except (TypeError, ValueError):
            self.speed = 1.0
        self.speed = max(0.5, min(2.0, self.speed))
        self.output_dir = settings.data_dir / "voice" / "out" / "tts"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return bool(self.api_key and self.voice_id)

    async def _request(self, client: httpx.AsyncClient, text: str) -> bytes:
        payload: dict = {
            "text": text,
            "model_id": self.model,
            "language_code": self.language_code,
        }
        if abs(self.speed - 1.0) > 1e-6:
            payload["voice_settings"] = {"speed": self.speed}
        response = await client.post(
            f"{self.base_url}/v1/text-to-speech/{quote(self.voice_id, safe='')}",
            params={"output_format": "wav_24000"},
            headers={"xi-api-key": self.api_key},
            json=payload,
        )
        if response.status_code >= 400:
            raise TTSError(
                f"Breeze TTS HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            raise TTSError("Breeze TTS 返回空音频")
        return response.content

    async def synthesize(self, text: str, output: Path | None = None) -> Path:
        if not self.available():
            raise TTSError("Breeze 音色未配置或缺少 API key/voice_id")
        chunks = split_for_tts(text, total_max=self.settings.tts_max_chars)
        if not chunks:
            raise TTSError("语音文本为空")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        token = uuid.uuid4().hex[:8]
        output = output or self.output_dir / f"breeze_{stamp}_{token}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            async with httpx.AsyncClient(timeout=180.0) as client:
                raws: list[Path] = []
                normalized: list[Path] = []
                for index, chunk in enumerate(chunks):
                    logger.info(
                        "Breeze TTS 合成 %s 段，第 %s 段 %s 字",
                        len(chunks),
                        index + 1,
                        len(chunk),
                    )
                    content = await self._request(client, chunk)
                    raw = output.with_name(f"{output.stem}_part{index}_raw.wav")
                    raw.write_bytes(content)
                    norm = await self._normalize(
                        raw, output.with_name(f"{output.stem}_part{index}.wav")
                    )
                    raws.append(raw)
                    normalized.append(norm)

            try:
                if len(normalized) == 1:
                    shutil.copyfile(normalized[0], output)
                else:
                    try:
                        self._concat_wavs(normalized, output)
                    except Exception as exc:  # noqa: BLE001 - fallback to ffmpeg
                        logger.warning("wav 拼接失败，改用 ffmpeg：%s", exc)
                        await self._concat_with_ffmpeg(normalized, output)
            finally:
                for path in raws + normalized:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        return output

    async def _concat_with_ffmpeg(self, files: list[Path], output: Path) -> None:
        silence = output.with_name(f"{output.stem}_silence.wav")
        await self._run_ffmpeg(
            [
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                "0.15",
                "-c:a",
                "pcm_s16le",
                str(silence),
            ]
        )
        list_file = output.with_name(f"{output.stem}_concat.txt")
        lines: list[str] = []
        for index, path in enumerate(files):
            lines.append(f"file '{path.as_posix()}'")
            if index < len(files) - 1:
                lines.append(f"file '{silence.as_posix()}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            await self._run_ffmpeg(
                [
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
        finally:
            for path in (silence, list_file):
                try:
                    path.unlink()
                except OSError:
                    pass


class TTSService:
    def __init__(self, settings: Settings, profile: dict | None = None):
        self.settings = settings
        self.profile = profile or {}
        self.siliconflow = SiliconFlowTTS(settings, self.profile)
        self.mimo = MiMoTTS(settings, self.profile)
        self.breeze = BreezeTTS(settings, self.profile)

    @property
    def provider(self) -> str:
        return str(self.profile.get("provider") or "siliconflow")

    @property
    def voice_name(self) -> str:
        return str(
            self.profile.get("name")
            or {
                "breeze": "Breeze 克隆音色",
                "mimo": "MiMo 备用音色",
            }.get(self.provider, "SiliconFlow 克隆音色")
        )

    def available(self) -> bool:
        if self.provider == "breeze":
            return self.breeze.available()
        if self.provider == "mimo":
            return self.mimo.available()
        return self.siliconflow.available() or self.mimo.available()

    async def synthesize(self, text: str, voice: str = "siliconflow") -> Path:
        if voice == "moli":
            if self.mimo.available():
                return await self.mimo.synthesize_preset(text)
            if self.siliconflow.available():
                return await self.siliconflow.synthesize(text)
            raise TTSError("没有可用的 TTS provider")
        if self.provider == "breeze":
            if self.breeze.available():
                return await self.breeze.synthesize(text)
            raise TTSError("Breeze 音色未配置或缺少 API key/voice_id")
        if self.provider == "mimo":
            if self.mimo.available():
                return await self.mimo.synthesize_preset(text)
            if self.siliconflow.available():
                return await self.siliconflow.synthesize(text)
            raise TTSError("没有可用的 TTS provider")
        if self.siliconflow.available():
            return await self.siliconflow.synthesize(text)
        if self.mimo.available():
            return await self.mimo.synthesize_preset(text)
        raise TTSError("没有可用的 TTS provider")


def build_tts(settings: Settings, profile: dict | None = None) -> TTSService | None:
    service = TTSService(settings, profile)
    return service if service.available() else None
