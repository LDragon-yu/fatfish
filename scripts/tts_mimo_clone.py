#!/usr/bin/env python3
"""MiMo V2.5 音色克隆 TTS 测试脚本。

只做一件事：读取一段参考音频，调用 MiMo `mimo-v2.5-tts-voiceclone`
合成一段语音，保存为 wav。参考音频和生成的音频都不会提交到 git。

用法：
    python scripts/tts_mimo_clone.py --dry-run
    python scripts/tts_mimo_clone.py --text "今天真的好累啊，你陪我说会儿话嘛。"

参考音频默认路径：private/voice/sample.wav
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "private" / "voice"
DEFAULT_OUTPUT = ROOT / "private" / "voice" / "out" / "mimo_clone_test.wav"
DEFAULT_TEXT = "今天真的好累啊，你陪我说会儿话嘛。"
DEFAULT_CONTEXT = "年轻女生，语气自然，带一点笑意，语速适中，像在 QQ 上发语音聊天"

MIME_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav"}
MAX_BASE64_BYTES = 10 * 1024 * 1024  # MiMo voiceclone 限制：base64 <= 10MB


def load_env() -> None:
    """加载项目根目录的 .env；没有 python-dotenv 时退回最小解析。"""
    env_path = ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except ImportError:
        pass

    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def probe_duration(path: Path) -> float | None:
    """用 ffprobe 读时长；失败时返回 None。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def find_default_sample() -> Path:
    env_path = os.getenv("MIMO_VOICE_SAMPLE", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    for name in ("sample.wav", "sample.mp3"):
        candidate = VOICE_DIR / name
        if candidate.exists():
            return candidate
    if VOICE_DIR.exists():
        audio_exts = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".amr"}
        for candidate in sorted(VOICE_DIR.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in audio_exts:
                return candidate
    return VOICE_DIR / "sample.wav"


def ensure_supported_sample(path: Path) -> Path:
    if path.suffix.lower() in MIME_TYPES:
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(
            f"参考音频格式 {path.suffix} 需要 ffmpeg 转成 wav，但找不到 ffmpeg"
        )
    output = path.with_name(path.stem + "_mono.wav")
    print(f"参考音频不是 mp3/wav，先转成单声道 24k wav：{output}")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg 转换失败：{exc}") from exc
    return output


def encode_voice(path: Path) -> tuple[str, int]:
    """把参考音频编码成 MiMo 需要的 data URL，并做格式/大小校验。"""
    suffix = path.suffix.lower()
    if suffix not in MIME_TYPES:
        raise SystemExit(f"参考音频只支持 .mp3 / .wav，当前是 {suffix or '(无扩展名)'}")

    raw = path.read_bytes()
    if not raw:
        raise SystemExit("参考音频是空文件")

    encoded = base64.b64encode(raw).decode("ascii")
    if len(encoded) > MAX_BASE64_BYTES:
        raise SystemExit(
            "参考音频 base64 后超过 10MB "
            f"（当前 {len(encoded) / 1024 / 1024:.2f}MB）。请剪短或重新压缩。"
        )
    return f"data:{MIME_TYPES[suffix]};base64,{encoded}", len(raw)


def read_text(args: argparse.Namespace) -> str:
    if args.text_file:
        path = Path(args.text_file).expanduser()
        if not path.exists():
            raise SystemExit(f"文本文件不存在：{path}")
        text = path.read_text(encoding="utf-8").strip()
    else:
        text = (args.text or "").strip()
    if not text:
        raise SystemExit("要合成的文本为空。用 --text 或 --text-file 传入。")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiMo V2.5 音色克隆 TTS 测试（使用你自己的参考音频）"
    )
    parser.add_argument(
        "--voice-file",
        default=None,
        help="参考音频路径（mp3/wav）。默认自动找 private/voice/ 下的第一个音频，"
        "或 MIMO_VOICE_SAMPLE 指定的文件",
    )
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要合成的文本")
    parser.add_argument("--text-file", help="从文件读取要合成的文本（优先于 --text）")
    parser.add_argument(
        "--context",
        default=DEFAULT_CONTEXT,
        help="自然语言风格控制（语气/情绪/语速/角色）",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="输出 wav 路径",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MIMO_TTS_MODEL", "mimo-v2.5-tts-voiceclone"),
        help="MiMo 模型名，默认 mimo-v2.5-tts-voiceclone",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="API 超时秒数，默认 180",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验参考音频和参数，不调用 API",
    )
    args = parser.parse_args()

    load_env()

    sample_path = (
        Path(args.voice_file).expanduser().resolve()
        if args.voice_file
        else find_default_sample()
    )
    if not sample_path.exists():
        raise SystemExit(
            f"参考音频不存在：{sample_path}\n"
            "请把录音放到 private/voice/，或用 --voice-file 指定路径。"
        )
    sample_path = ensure_supported_sample(sample_path)

    text = read_text(args)
    duration = probe_duration(sample_path)
    data_url, raw_size = encode_voice(sample_path)

    print(f"参考音频 : {sample_path}")
    print(f"音频大小 : {raw_size / 1024:.1f} KiB")
    print(f"音频时长 : {duration:.2f}s" if duration is not None else "音频时长 : 未知（缺 ffprobe）")
    print(f"合成文本 : {len(text)} 字")
    print(f"风格控制 : {args.context or '(空)'}")
    print(f"模型     : {args.model}")
    print(f"输出路径 : {Path(args.output).expanduser().resolve()}")

    if duration is not None and not 8 <= duration <= 30:
        print(
            "提示：MiMo 建议参考音频 10–30 秒，当前时长不在这个范围，"
            "克隆相似度可能下降。",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\ndry-run：校验通过，未调用 API。")
        print(f"参考音频 base64 长度：{len(data_url)} 字符")
        return 0

    api_key = os.getenv("MIMO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "没有找到 MIMO_API_KEY。请复制 .env.example 为 .env，填入 key，"
            "并执行 chmod 600 .env。"
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "缺少 openai 依赖。请先执行：pip install -r requirements.txt"
        ) from exc

    base_url = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=args.timeout)

    messages: list[dict[str, str]] = []
    if args.context:
        # MiMo 用 user 消息承载风格/导演指令，assistant 消息里的文本才会被合成
        messages.append({"role": "user", "content": args.context})
    messages.append({"role": "assistant", "content": text})

    try:
        completion = client.chat.completions.create(
            model=args.model,
            messages=messages,
            audio={"format": "wav", "voice": data_url},
        )
    except Exception as exc:  # noqa: BLE001 - 直接把 API 错误展示给用户
        print(f"MiMo API 调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    message = completion.choices[0].message
    audio = getattr(message, "audio", None)
    if audio is None or not getattr(audio, "data", None):
        print("MiMo 没有返回音频数据。", file=sys.stderr)
        return 1

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(audio.data))
    print(f"\n已保存：{output_path}（{output_path.stat().st_size / 1024:.1f} KiB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
