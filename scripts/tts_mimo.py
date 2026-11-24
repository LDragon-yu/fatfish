#!/usr/bin/env python3
"""MiMo V2.5 预置音色 TTS 测试（默认茉莉）。

用法：
    python scripts/tts_mimo.py --dry-run
    python scripts/tts_mimo.py --text "今天真的好累啊，你陪我说会儿话嘛。"

需要 .env 里的 MIMO_API_KEY。参考音频不需要；克隆声请用 tts_mimo_clone.py。
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "private" / "voice" / "out" / "mimo_moli_test.wav"
DEFAULT_TEXT = "今天真的好累啊，你陪我说会儿话嘛。"
DEFAULT_CONTEXT = "年轻女生，语气自然，带一点笑意，语速适中，像在 QQ 上发语音聊天"
PRESET_VOICES = ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]


def load_env() -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="MiMo V2.5 预置音色 TTS 测试")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要合成的文本")
    parser.add_argument(
        "--voice",
        default=os.getenv("MIMO_TTS_VOICE", "茉莉"),
        choices=PRESET_VOICES,
        help="预置音色，默认茉莉",
    )
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help="自然语言风格控制")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 wav 路径")
    parser.add_argument(
        "--model",
        default=os.getenv("MIMO_TTS_MODEL", "mimo-v2.5-tts"),
        help="MiMo 模型名，默认 mimo-v2.5-tts",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    text = args.text.strip()
    if not text:
        raise SystemExit("--text 不能为空")

    print(f"模型     : {args.model}")
    print(f"音色     : {args.voice}")
    print(f"风格     : {args.context or '(空)'}")
    print(f"文本     : {len(text)} 字")
    print(f"输出路径 : {Path(args.output).expanduser().resolve()}")

    if args.dry_run:
        print("\ndry-run：参数校验通过，未调用 API。")
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
        raise SystemExit("缺少 openai 依赖：pip install -r requirements.txt") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
        timeout=args.timeout,
    )
    messages = []
    if args.context:
        messages.append({"role": "user", "content": args.context})
    messages.append({"role": "assistant", "content": text})

    try:
        completion = client.chat.completions.create(
            model=args.model,
            messages=messages,
            audio={"format": "wav", "voice": args.voice},
        )
    except Exception as exc:  # noqa: BLE001
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
