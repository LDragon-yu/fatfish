#!/usr/bin/env python3
"""交互式写入 API key 到 .env，不回显、不经过 shell history。

用法：
    . .venv/bin/activate
    python scripts/setup_secrets.py

说明：
- 直接回车 = 保留当前值；
- 输入 clear = 清空该 key；
- 写入后 .env 权限强制为 600；
- 脚本不会打印 key 内容，只显示“已设置（长度 N）”。
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"
EXAMPLE_ENV = ROOT / ".env.example"

SECRETS = [
    ("DEEPSEEK_API_KEY", "DeepSeek API key（聊天模型）"),
    ("MIMO_API_KEY", "MiMo API key（茉莉 / 克隆 TTS）"),
    ("SILICONFLOW_API_KEY", "SiliconFlow API key（SenseVoice ASR）"),
    ("BREEZE_API_KEY", "Breeze Blue API key（可选 TTS / 克隆）"),
]


def load_lines(path: Path) -> list[str]:
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    if EXAMPLE_ENV.exists():
        return EXAMPLE_ENV.read_text(encoding="utf-8").splitlines()
    return []


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_line(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    out: list[str] = []
    found = False
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    return out


def mask(value: str) -> str:
    return f"已设置（长度 {len(value)}）" if value else "未设置"


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="交互式写入 fatfish API key")
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV),
        help="要写入的 .env 路径（默认项目根目录 .env）",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser()
    lines = load_lines(env_path)
    current = parse_env(lines)

    print(f"写入文件：{env_path}")
    print("输入不会回显；直接回车保留当前值；输入 clear 清空。\n")

    for key, label in SECRETS:
        existing = current.get(key, "")
        print(f"{label}")
        print(f"  当前：{mask(existing)}")
        try:
            raw = getpass.getpass(f"  输入 {key}（回车保留）：")
        except (KeyboardInterrupt, EOFError):
            print("\n已取消，未写入任何内容。")
            return 1

        value = raw.strip()
        if not value:
            value = existing
        elif value.lower() == "clear":
            value = ""
        lines = update_line(lines, key, value)
        current[key] = value
        print(f"  -> {mask(value)}\n")

    text = "\n".join(lines).rstrip() + "\n"
    write_private(env_path, text)
    print(f"已写入 {env_path}（权限 600）。")
    print("\n下一步：")
    print("  . .venv/bin/activate")
    print("  python scripts/tts_mimo.py --dry-run")
    print("  python scripts/tts_mimo.py")
    print("  python -m fatfish        # 真实模型模式（有 DEEPSEEK_API_KEY 时）")
    print("  python -m fatfish.cli")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
