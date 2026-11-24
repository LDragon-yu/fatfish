from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .llm import LLMClient

MEDIA_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
DESCRIBE_MAX_CHARS = 120
DESCRIBE_FRAME_LIMIT = 6

IMAGE_PROMPT = (
    "用一句简短的中文描述这张图片的内容：画面里是谁/什么、在做什么、能看清的细节。"
    "只输出这一句话，不要引号，不要解释。"
)
VIDEO_PROMPT = (
    "这是他发来的一段视频的 {count} 张关键帧截图{extra}。"
    "用一句简短的中文描述视频里发生了什么。只输出这一句话，不要引号，不要解释。"
)


def resolve_media_path(data_dir: Path, raw_path: Any) -> Path | None:
    """Resolve a stored media path, refusing anything outside data_dir."""
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = data_dir / path
    try:
        resolved = path.resolve()
        root = data_dir.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def image_part_for_path(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_INLINE_IMAGE_BYTES:
            return None
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    mime = MEDIA_MIME.get(path.suffix.lower(), "image/jpeg")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def image_part(data_dir: Path, raw_path: Any) -> dict[str, Any] | None:
    path = resolve_media_path(data_dir, raw_path)
    return image_part_for_path(path) if path is not None else None


def clean_description(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for line in raw.splitlines():
        line = line.strip().strip("\"'“”‘’「」").strip()
        if line:
            raw = line
            break
    for prefix in ("[图片]", "[视频]", "图片：", "图片:", "视频：", "视频:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :].strip()
    return raw[:DESCRIBE_MAX_CHARS].strip()


async def describe_image(llm: LLMClient, path: Path) -> str:
    part = image_part_for_path(path)
    if part is None:
        return ""
    response = await llm.chat(
        [{"role": "user", "content": [{"type": "text", "text": IMAGE_PROMPT}, part]}]
    )
    return clean_description(response.content)


async def describe_video(
    llm: LLMClient,
    frames: list[Path],
    transcript: str = "",
    duration: float | None = None,
) -> str:
    selected: list[Path] = []
    if frames:
        step = max(1, len(frames) // DESCRIBE_FRAME_LIMIT)
        selected = frames[::step][:DESCRIBE_FRAME_LIMIT]
    extra = ""
    if duration:
        extra += f"，约 {int(float(duration))} 秒"
    if transcript:
        extra += f"，音轨转写：{transcript[:600]}"
    if not selected and not transcript:
        return ""
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": VIDEO_PROMPT.format(count=len(selected) or len(frames), extra=extra),
        }
    ]
    for frame in selected:
        part = image_part_for_path(frame)
        if part is not None:
            content.append(part)
    if not selected:
        content[0]["text"] = (
            f"这是他发来的一段视频{extra}。用一句简短的中文描述这段视频。"
            "只输出这一句话，不要引号，不要解释。"
        )
    response = await llm.chat([{"role": "user", "content": content}])
    return clean_description(response.content)
