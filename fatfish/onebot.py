from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from .asr import SiliconFlowASR
from .config import Settings
from .engine import Engine
from .qq_faces import QQ_FACE_NAMES
from . import users

logger = logging.getLogger(__name__)

IMAGE_MAX_MB_DEFAULT = 10
VIDEO_MAX_MB_DEFAULT = 80
MAX_IMAGES_PER_MESSAGE = 4
MAX_VIDEOS_PER_MESSAGE = 1
IMAGE_MAX_SIDE_DEFAULT = 1600
FRAME_MAX_SIDE = 1280
MAX_TEXT_SEGMENTS = 20
TEXT_SEGMENT_DELAY = 0.35
INBOUND_DELAY_MIN = 5.0
INBOUND_DELAY_MAX = 8.0
TYPING_CHARS_PER_SECOND = 4.0
TYPING_MIN_DELAY = 0.6
TYPING_MAX_DELAY = 8.0
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".flv", ".webm"}


def build_text_message(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "data": {"text": text}}]


QQ_FACE_IDS: dict[str, str] = {}
for _face_id, _face_name in QQ_FACE_NAMES.items():
    QQ_FACE_IDS.setdefault(_face_name, _face_id)

FACE_MARKER_RE = re.compile(r"\[QQ表情:([^\]×]+?)(?:×(\d+))?\]")
MFACE_MARKER_RE = re.compile(r"\[表情包(?::[^\]]*)?\]")


def build_rich_message(text: str) -> list[dict[str, Any]]:
    """把模型文本里的 [QQ表情:名称] 转成真实的 OneBot face 段。

    [表情包:...] 无法可靠地重发，直接去掉；未知表情名也去掉。
    """
    cleaned = MFACE_MARKER_RE.sub("", str(text or ""))
    parts: list[dict[str, Any]] = []
    position = 0
    for match in FACE_MARKER_RE.finditer(cleaned):
        before = cleaned[position:match.start()].strip()
        if before:
            parts.append({"type": "text", "data": {"text": before}})
        name = match.group(1).strip()
        try:
            count = max(1, min(5, int(match.group(2) or 1)))
        except (TypeError, ValueError):
            count = 1
        face_id = QQ_FACE_IDS.get(name)
        if face_id:
            for _ in range(count):
                parts.append({"type": "face", "data": {"id": face_id}})
        position = match.end()
    tail = cleaned[position:].strip()
    if tail:
        parts.append({"type": "text", "data": {"text": tail}})
    return parts


def build_record_message(audio_path: Path) -> list[dict[str, Any]]:
    return [{"type": "record", "data": {"file": Path(audio_path).resolve().as_uri()}}]


def face_label(data: dict[str, Any]) -> str:
    face_id = str(data.get("id") or "").strip()
    name = QQ_FACE_NAMES.get(face_id)
    label = f"QQ表情:{name}" if name else f"QQ表情:{face_id or '未知'}"
    try:
        chain = int(data.get("chainCount") or 0)
    except (TypeError, ValueError):
        chain = 0
    if chain > 1:
        label += f"×{chain}"
    return f"[{label}]"


def mface_label(data: dict[str, Any]) -> str:
    summary = str(data.get("summary") or "").strip()
    if summary and summary not in {"[动画表情]", "[表情]"}:
        return f"[表情包:{summary}]"
    return "[表情包]"


def typing_delay_for(text: str, floor: float = 0.0) -> float:
    """按下一段文字长度模拟打字时间。"""
    length = len(str(text or "").strip())
    if length <= 0:
        return 0.0
    return max(
        float(floor),
        TYPING_MIN_DELAY,
        min(TYPING_MAX_DELAY, length / TYPING_CHARS_PER_SECOND),
    )


def split_text_segments(text: str, max_segments: int | None = None) -> list[str]:
    """QQ 聊天习惯：模型回复里的每个回车段落单独发一条。"""
    segments = [line.strip() for line in str(text or "").splitlines()]
    segments = [line for line in segments if line]
    if not segments:
        return []
    limit = max(1, int(max_segments or MAX_TEXT_SEGMENTS))
    if len(segments) > limit:
        return segments[: limit - 1] + ["\n".join(segments[limit - 1 :])]
    return segments


def parse_private_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("post_type") != "message":
        return None
    if event.get("message_type") != "private":
        return None

    text_parts: list[str] = []
    records: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    faces: list[dict[str, Any]] = []
    mfaces: list[dict[str, Any]] = []
    for segment in event.get("message") or []:
        if not isinstance(segment, dict):
            continue
        kind = segment.get("type")
        data = segment.get("data") or {}
        if kind == "text":
            text_parts.append(str(data.get("text") or ""))
        elif kind == "record":
            records.append(data)
        elif kind == "image":
            images.append(data)
        elif kind == "video":
            videos.append(data)
        elif kind == "face":
            faces.append(data)
        elif kind == "mface":
            mfaces.append(data)

    return {
        "text": "".join(text_parts).strip(),
        "records": records,
        "images": images,
        "videos": videos,
        "faces": faces,
        "mfaces": mfaces,
        "message_id": event.get("message_id"),
        "time": event.get("time"),
    }


class OneBotAdapter:
    """OneBot 11 reverse WebSocket adapter for NapCat (private messages only)."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        asr: SiliconFlowASR | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.asr = asr
        self.self_id = ""
        self._active: WebSocket | None = None
        self._batches: dict[int, list[dict[str, Any]]] = {}
        self._batch_tasks: dict[int, asyncio.Task[None]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._queue: asyncio.Queue | None = None
        self._forward_task: asyncio.Task[None] | None = None

    def _check_auth(self, websocket: WebSocket) -> bool:
        token = self.settings.onebot_access_token
        if not token:
            return True
        supplied = websocket.query_params.get("access_token", "")
        if not supplied:
            auth = websocket.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        return supplied == token

    async def handle(self, websocket: WebSocket) -> None:
        if not self.settings.onebot_enabled:
            await websocket.close(code=1008, reason="onebot disabled")
            return
        if not self._check_auth(websocket):
            logger.warning("OneBot 连接鉴权失败")
            await websocket.close(code=1008, reason="bad access token")
            return
        if self._active is not None:
            logger.warning("已有 OneBot 连接，拒绝新连接")
            await websocket.close(code=1013, reason="already connected")
            return

        await websocket.accept()
        self._active = websocket
        self._queue = self.engine.bus.subscribe()
        self._forward_task = asyncio.create_task(
            self._forward_loop(), name="fatfish-onebot-forward"
        )
        logger.info("NapCat OneBot 已连接")

        try:
            while True:
                data = await websocket.receive_json()
                if not isinstance(data, dict):
                    continue
                if data.get("self_id"):
                    self.self_id = str(data.get("self_id"))
                echo = data.get("echo")
                if echo is not None:
                    future = self._pending.pop(str(echo), None)
                    if future is not None and not future.done():
                        future.set_result(data)
                    continue
                if data.get("post_type") == "message":
                    await self._handle_message(data)
                elif data.get("post_type") == "meta_event":
                    if data.get("meta_event_type") == "lifecycle":
                        logger.info(
                            "OneBot lifecycle: %s（登录账号 self_id=%s）",
                            data.get("sub_type"),
                            data.get("self_id"),
                        )
        except WebSocketDisconnect as exc:
            logger.info("NapCat OneBot 断开 (code=%s reason=%s)", exc.code, exc.reason)
        except Exception:  # noqa: BLE001 - keep the app alive
            logger.exception("OneBot 接收循环异常")
        finally:
            await self._cleanup()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.onebot_enabled,
            "connected": self._active is not None,
            "self_id": self.self_id,
            "owner_qq": self.settings.owner_qq,
        }

    async def _cleanup(self) -> None:
        if self._forward_task is not None:
            self._forward_task.cancel()
            try:
                await self._forward_task
            except asyncio.CancelledError:
                pass
            self._forward_task = None
        if self._queue is not None:
            self.engine.bus.unsubscribe(self._queue)
            self._queue = None
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ConnectionError("OneBot disconnected"))
        self._pending.clear()
        for task in list(self._batch_tasks.values()):
            task.cancel()
        self._batch_tasks.clear()
        self._batches.clear()
        self._active = None

    async def _forward_loop(self) -> None:
        if self._queue is None:
            return
        try:
            while True:
                result = await self._queue.get()
                user = await users.get_user(self.engine.db, int(result.user_id))
                if not users.is_active(user):
                    logger.warning(
                        "丢弃发往非 active 用户 %s 的消息", result.user_id
                    )
                    continue
                for item in result.outgoing:
                    if item.delay:
                        await asyncio.sleep(min(max(item.delay, 0.0), 60.0))
                    await self._send_outgoing(item, user)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("OneBot 发送循环异常")

    async def _handle_message(self, event: dict[str, Any]) -> None:
        parsed = parse_private_event(event)
        if parsed is None:
            return

        sender_id = str(event.get("user_id") or "").strip()
        if not sender_id:
            return
        db = self.engine.db
        user = await users.resolve(db, "qq", sender_id)
        if not users.is_active(user):
            await users.record_contact(db, "qq", sender_id)
            if user is None:
                logger.info("收到未登记 QQ %s 的私聊，已记为 pending", sender_id)
            return
        user_id = int(user["id"])

        # 5–8 秒防抖：连续发来的消息合并成一轮，避免逐条抢答
        self._batches.setdefault(user_id, []).append(parsed)
        previous = self._batch_tasks.pop(user_id, None)
        if previous is not None and not previous.done():
            previous.cancel()
        delay = random.uniform(INBOUND_DELAY_MIN, INBOUND_DELAY_MAX)
        self._batch_tasks[user_id] = asyncio.create_task(
            self._flush_batch_after(user_id, delay),
            name=f"fatfish-batch-{user_id}",
        )

    async def _flush_batch_after(self, user_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        events = self._batches.pop(user_id, [])
        self._batch_tasks.pop(user_id, None)
        if not events:
            return
        user = await users.get_user(self.engine.db, user_id)
        if not users.is_active(user):
            return
        try:
            await self._process_batch(user, events)
        except Exception:  # noqa: BLE001
            logger.exception("批量消息处理失败")

    async def _process_batch(
        self, user: dict[str, Any], events: list[dict[str, Any]]
    ) -> None:
        user_id = int(user["id"])
        parts: list[str] = []
        media: list[dict[str, Any]] = []
        has_voice = False

        for parsed in events:
            if parsed["text"]:
                parts.append(parsed["text"])
            for data in parsed["faces"]:
                parts.append(face_label(data))
            for data in parsed["mfaces"]:
                parts.append(mface_label(data))

            for record in parsed["records"]:
                has_voice = True
                try:
                    path = await self._record_path(record)
                    if self.asr is None:
                        parts.append("[语音]")
                        self._cleanup_temp(path)
                        continue
                    text = await self.asr.transcribe(path)
                    parts.append(f"[语音：{text}]" if text else "[语音：没听清]")
                    self._cleanup_temp(path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("语音消息处理失败：%s", exc)
                    parts.append("[语音：没听清]")

            if self.settings.media_enabled:
                for data in parsed["images"][:MAX_IMAGES_PER_MESSAGE]:
                    try:
                        path = await self._prepare_image(data)
                        media.append({"kind": "image", "path": self._rel_media(path)})
                        desc = (
                            await self.engine.describe_image(path)
                            if self.engine is not None
                            else ""
                        )
                        parts.append(f"[图片]{desc}" if desc else "[图片]")
                    except Exception as exc:  # noqa: BLE001 - keep the turn going
                        logger.warning("图片处理失败：%s", exc)
                        parts.append("[图片：加载失败]")

                for data in parsed["videos"][:MAX_VIDEOS_PER_MESSAGE]:
                    try:
                        item = await self._prepare_video(data)
                        media.append(item)
                        desc = ""
                        if self.engine is not None:
                            frames = [
                                self.settings.data_dir / frame
                                for frame in item.get("frames") or []
                            ]
                            desc = await self.engine.describe_video(
                                frames,
                                item.get("transcript") or "",
                                item.get("duration"),
                            )
                        parts.append(f"[视频]{desc}" if desc else "[视频]")
                    except Exception as exc:  # noqa: BLE001 - keep the turn going
                        logger.warning("视频处理失败：%s", exc)
                        parts.append("[视频：加载失败]")
            else:
                parts.extend(
                    "[图片]" for _ in parsed["images"][:MAX_IMAGES_PER_MESSAGE]
                )
                parts.extend(
                    "[视频]" for _ in parsed["videos"][:MAX_VIDEOS_PER_MESSAGE]
                )

        text = " ".join(part for part in parts if part).strip()
        if not text:
            text = "[发来一条空消息]"

        ids = [
            str(parsed["message_id"])
            for parsed in events
            if parsed.get("message_id") is not None
        ]
        if len(ids) == 1:
            source_id = ids[0]
        elif ids:
            source_id = f"batch-{ids[0]}-{ids[-1]}"
        else:
            source_id = uuid.uuid4().hex

        try:
            await self.engine.handle_user_message(
                user_id,
                text,
                channel="voice" if has_voice else "text",
                source_id=source_id,
                media=media or None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("OneBot 消息处理失败")

    def _media_dir(self, name: str) -> Path:
        directory = self.settings.data_dir / "media" / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _rel_media(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.settings.data_dir.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _media_suffix(self, data: dict[str, Any], kind: str) -> str:
        raw_file = str(data.get("file") or "")
        url = str(data.get("url") or "")
        suffix = Path(raw_file).suffix.lower()
        if not suffix:
            suffix = Path(url.split("?")[0]).suffix.lower()
        allowed = IMAGE_SUFFIXES if kind == "image" else VIDEO_SUFFIXES
        return suffix if suffix in allowed else (".jpg" if kind == "image" else ".mp4")

    async def _obtain_media_source(
        self, data: dict[str, Any], kind: str
    ) -> tuple[Path, bool]:
        """Return (local path, owns_temp). Owned temp files must be deleted."""
        limit_mb = (
            self.settings.image_max_mb
            if kind == "image"
            else self.settings.video_max_mb
        )
        limit = max(1, int(limit_mb)) * 1024 * 1024
        raw_file = str(data.get("file") or "").strip()
        url = str(data.get("url") or "").strip()
        if raw_file.startswith("file://"):
            raw_file = raw_file[7:]
        if raw_file and Path(raw_file).exists():
            source = Path(raw_file)
            if source.stat().st_size > limit:
                raise RuntimeError("文件超过大小限制")
            return source, False

        if not url.startswith(("http://", "https://")) and raw_file and kind == "image":
            response = await self._call_action("get_image", {"file": raw_file})
            if self._ok(response):
                info = response.get("data") or {}
                candidate = str(info.get("file") or "")
                if candidate.startswith("file://"):
                    candidate = candidate[7:]
                if candidate and Path(candidate).exists():
                    return Path(candidate), False
                url = str(info.get("url") or "") or url

        if not url.startswith(("http://", "https://")):
            raise FileNotFoundError("没有可下载的地址")

        suffix = self._media_suffix(data, kind)
        target = self._media_dir("tmp") / f"{uuid.uuid4().hex}{suffix}"
        timeout = 120.0 if kind == "video" else 60.0
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    size = 0
                    with target.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > limit:
                                raise RuntimeError("文件超过大小限制")
                            handle.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if target.stat().st_size <= 0:
            target.unlink(missing_ok=True)
            raise RuntimeError("下载到空文件")
        return target, True

    async def _run_ffmpeg(self, args: list[str]) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.settings.ffmpeg_path,
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"找不到 ffmpeg：{self.settings.ffmpeg_path}") from exc
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 失败：{stderr.decode(errors='ignore')[:300]}")

    async def _prepare_image(self, data: dict[str, Any]) -> Path:
        source, owned = await self._obtain_media_source(data, "image")
        output = self._media_dir("images") / f"{uuid.uuid4().hex}.jpg"
        max_side = max(256, int(self.settings.image_max_side))
        try:
            await self._run_ffmpeg(
                [
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale=w=min({max_side}\\,iw):h=-2",
                    "-q:v",
                    "3",
                    str(output),
                ]
            )
        finally:
            if owned:
                source.unlink(missing_ok=True)
        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("图片转码失败")
        return output

    def _ffprobe_path(self) -> str:
        ffmpeg = Path(self.settings.ffmpeg_path)
        if ffmpeg.name.lower().startswith("ffmpeg"):
            candidate = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
            if candidate.exists():
                return str(candidate)
        return "ffprobe"

    async def _video_duration(self, video: Path) -> float | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._ffprobe_path(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            return float(out.decode().strip())
        except Exception:  # noqa: BLE001 - duration is best effort
            return None

    async def _extract_frames(
        self, video: Path, duration: float | None
    ) -> list[Path]:
        max_frames = max(1, int(self.settings.video_max_frames))
        if duration and duration > max_frames:
            fps = max_frames / duration
        else:
            fps = max(0.05, float(self.settings.video_fps))
        prefix = f"frame_{uuid.uuid4().hex}"
        pattern = self._media_dir("frames") / f"{prefix}_%02d.jpg"
        try:
            await self._run_ffmpeg(
                [
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-vf",
                    f"fps={fps:.4f},scale=w=min({FRAME_MAX_SIDE}\\,iw):h=-2",
                    "-frames:v",
                    str(max_frames),
                    "-q:v",
                    "3",
                    str(pattern),
                ]
            )
        except Exception as exc:  # noqa: BLE001 - transcript-only fallback
            logger.warning("视频抽帧失败：%s", exc)
            return []
        return sorted(self._media_dir("frames").glob(f"{prefix}_*.jpg"))

    async def _video_transcript(self, video: Path) -> str:
        if self.asr is None:
            return ""
        try:
            wav = await self._to_wav(video)
        except Exception as exc:  # noqa: BLE001
            logger.warning("视频音轨提取失败：%s", exc)
            return ""
        try:
            text = await self.asr.transcribe(wav)
            return (text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("视频音轨识别失败：%s", exc)
            return ""
        finally:
            self._cleanup_temp(wav)

    async def _prepare_video(self, data: dict[str, Any]) -> dict[str, Any]:
        source, owned = await self._obtain_media_source(data, "video")
        suffix = source.suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            suffix = ".mp4"
        video = self._media_dir("videos") / f"{uuid.uuid4().hex}{suffix}"
        frames: list[Path] = []
        try:
            if owned:
                os.replace(source, video)
            else:
                shutil.copyfile(source, video)
            duration = await self._video_duration(video)
            frames = await self._extract_frames(video, duration)
            transcript = await self._video_transcript(video)
        except Exception:
            video.unlink(missing_ok=True)
            for frame in frames:
                frame.unlink(missing_ok=True)
            raise
        return {
            "kind": "video",
            "path": self._rel_media(video),
            "frames": [self._rel_media(frame) for frame in frames],
            "transcript": transcript,
            "duration": round(duration, 1) if duration else None,
        }

    async def _record_path(self, data: dict[str, Any]) -> Path:
        raw_file = str(data.get("file") or "").strip()
        url = str(data.get("url") or "").strip()

        if raw_file.startswith("file://"):
            raw_file = raw_file[7:]
        if raw_file and Path(raw_file).exists():
            return await self._to_wav(Path(raw_file))

        if url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url)
            response.raise_for_status()
            suffix = Path(url.split("?")[0]).suffix or ".amr"
            fd, name = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            temp = Path(name)
            temp.write_bytes(response.content)
            return await self._to_wav(temp)

        if raw_file:
            response = await self._call_action(
                "get_record", {"file": raw_file, "out_format": "wav"}
            )
            if self._ok(response):
                path = str((response.get("data") or {}).get("file") or "")
                if path.startswith("file://"):
                    path = path[7:]
                if path and Path(path).exists():
                    return await self._to_wav(Path(path))

        raise FileNotFoundError(f"拿不到语音文件：{raw_file or url or '(空)'}")

    async def _to_wav(self, source: Path) -> Path:
        tmp_dir = self.settings.data_dir / "tmp" / "onebot"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        output = tmp_dir / f"voice_{uuid.uuid4().hex[:12]}.wav"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.settings.ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"找不到 ffmpeg：{self.settings.ffmpeg_path}"
            ) from exc
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 转换语音失败：{stderr.decode(errors='ignore')[:300]}")
        return output

    def _cleanup_temp(self, path: Path) -> None:
        try:
            if self.settings.data_dir / "tmp" in path.parents:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    async def _send_outgoing(self, item, user: dict[str, Any]) -> None:
        target = int(user["platform_id"])
        if item.kind == "voice":
            audio_path = item.meta.get("audio_path")
            if audio_path:
                path = Path(audio_path)
                if path.exists():
                    response = await self._call_action(
                        "send_private_msg",
                        {
                            "user_id": target,
                            "message": build_record_message(path),
                        },
                    )
                    if self._ok(response):
                        return
                    logger.warning("发送 QQ 语音失败，回退文字：%s", response)
                else:
                    logger.warning("TTS 音频不存在，回退文字：%s", path)
            await self._send_text(item.text, user)
            return
        await self._send_text(item.text, user)

    async def _send_text(self, text: str, user: dict[str, Any]) -> None:
        target = int(user["platform_id"])
        if self.settings.split_messages:
            segments = split_text_segments(text, self.settings.split_max_segments)
        else:
            stripped = str(text or "").strip()
            segments = [stripped] if stripped else []
        if not segments:
            return
        logger.info("发送 QQ 文字 %s 段 -> %s", len(segments), target)
        sent = 0
        for segment in segments:
            message = build_rich_message(segment)
            if not message:
                continue
            if sent > 0:
                delay = typing_delay_for(
                    segment, floor=self.settings.split_delay_seconds
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            response = await self._call_action(
                "send_private_msg",
                {
                    "user_id": target,
                    "message": message,
                },
            )
            if not self._ok(response):
                logger.warning("发送 QQ 文字失败：%s", response)
                return
            sent += 1

    async def _call_action(
        self, action: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any] | None:
        if self._active is None:
            raise ConnectionError("OneBot 未连接")
        echo = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[echo] = future
        await self._active.send_json(
            {"action": action, "params": params, "echo": echo}
        )
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning("OneBot action 超时：%s", action)
            return None
        finally:
            self._pending.pop(echo, None)

    @staticmethod
    def _ok(response: dict[str, Any] | None) -> bool:
        return bool(response) and (
            response.get("retcode") == 0
            or response.get("status") in {"ok", "async"}
        )
