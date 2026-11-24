#!/usr/bin/env python3
"""SiliconFlow SenseVoiceSmall 语音识别测试。

用法：
    python scripts/asr_siliconflow.py --file private/voice/out/mimo_moli_test.wav
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

import httpx

# Windows 管道重定向时默认用 GBK，识别文本里的 emoji 会打印崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"


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
    parser = argparse.ArgumentParser(description="SiliconFlow SenseVoiceSmall ASR 测试")
    parser.add_argument("--file", required=True, help="音频文件路径")
    parser.add_argument(
        "--model",
        default=os.getenv("SILICONFLOW_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall"),
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    load_env()
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("没有找到 SILICONFLOW_API_KEY，请先运行 scripts/setup_secrets.py")

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"音频不存在：{path}")

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    print(f"文件 : {path}")
    print(f"模型 : {args.model}")
    print(f"大小 : {path.stat().st_size / 1024:.1f} KiB")

    try:
        with path.open("rb") as handle:
            response = httpx.post(
                args.url,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": args.model},
                files={"file": (path.name, handle, mime)},
                timeout=args.timeout,
            )
    except httpx.HTTPError as exc:
        print(f"请求失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if response.status_code >= 400:
        print(
            f"ASR HTTP {response.status_code}: {response.text[:500]}",
            file=sys.stderr,
        )
        return 1

    try:
        payload = response.json()
    except ValueError:
        print(f"返回不是 JSON：{response.text[:500]}", file=sys.stderr)
        return 1

    text = str(payload.get("text") or "").strip()
    print("\n识别结果：")
    print(text or "(空)")
    return 0 if text else 2


if __name__ == "__main__":
    raise SystemExit(main())
