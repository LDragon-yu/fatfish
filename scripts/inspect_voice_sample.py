#!/usr/bin/env python3
"""检查参考音频是否符合 MiMo / SiliconFlow 的克隆要求。

用法：
    python scripts/inspect_voice_sample.py --file private/voice/sample.wav
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

MAX_BASE64_BYTES = 10 * 1024 * 1024


def ffprobe(path: Path) -> dict:
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        raise SystemExit("找不到 ffprobe，请先安装 ffmpeg。")
    result = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查音色克隆参考音频")
    parser.add_argument("--file", required=True, help="参考音频路径（mp3/wav）")
    args = parser.parse_args()

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 1
    if path.suffix.lower() not in {".mp3", ".wav"}:
        print("警告：MiMo 只支持 mp3 / wav；当前扩展名不是这两种。", file=sys.stderr)

    try:
        info = ffprobe(path)
    except subprocess.CalledProcessError as exc:
        print(f"ffprobe 读取失败：{exc}", file=sys.stderr)
        return 1

    audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        print("没有找到音频流。", file=sys.stderr)
        return 1

    stream = audio_streams[0]
    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or stream.get("duration") or 0)
    size = path.stat().st_size
    base64_size = (size + 2) // 3 * 4

    print(f"文件      : {path}")
    print(f"大小      : {size / 1024:.1f} KiB")
    print(f"时长      : {duration:.2f}s")
    print(f"编码      : {stream.get('codec_name', '?')}")
    print(f"采样率    : {stream.get('sample_rate', '?')} Hz")
    print(f"声道      : {stream.get('channels', '?')}")
    print(f"base64 后 : {base64_size / 1024 / 1024:.2f} MiB")

    warnings: list[str] = []
    if not 8 <= duration <= 30:
        warnings.append("时长建议 10–30 秒（SiliconFlow 推荐 8–10 秒），当前不在范围。")
    if stream.get("channels") != 1:
        warnings.append("建议单声道；多声道可能降低克隆稳定性。")
    sample_rate = int(stream.get("sample_rate") or 0)
    if sample_rate and sample_rate < 16000:
        warnings.append("采样率低于 16 kHz，建议 16 kHz 以上。")
    if base64_size > MAX_BASE64_BYTES:
        warnings.append("base64 后超过 MiMo 的 10MB 上限，请剪短或压缩。")
    if path.suffix.lower() == ".wav" and size > 20 * 1024 * 1024:
        warnings.append("wav 文件较大，可能超过 10MB base64 上限；可转成 192kbps mp3。")

    if warnings:
        print("\n注意事项：")
        for warning in warnings:
            print(f"- {warning}")
        print("\n可用 ffmpeg 转成单声道 24k wav：")
        print(f"  ffmpeg -y -i {path} -ac 1 -ar 24000 -c:a pcm_s16le {path.with_name(path.stem + '_mono.wav')}")
    else:
        print("\n检查通过：格式、时长和大小都适合用于克隆。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
