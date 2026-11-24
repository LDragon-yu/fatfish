#!/usr/bin/env python3
"""SiliconFlow CosyVoice2 克隆语音测试。

两种模式：
- references（默认优先）：直接在 /audio/speech 里带参考音频 + 文字稿，零样本克隆；
- upload：先把参考音频上传为自定义音色，拿 voice URI，再合成；
- auto：先试 references，失败再用 upload。

用法：
    . .venv/bin/activate
    python scripts/tts_siliconflow_clone.py --dry-run
    python scripts/tts_siliconflow_clone.py \
      --reference-audio private/voice/simple.mp3 \
      --reference-text-file private/voice/simple.txt \
      --output private/voice/out/siliconflow_clone_long.wav
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "private" / "voice"
DEFAULT_REFERENCE = VOICE_DIR / "simple.mp3"
DEFAULT_OUTPUT = ROOT / "private" / "voice" / "out" / "siliconflow_clone_test.wav"
DEFAULT_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
DEFAULT_TEXT = (
    "今天真的好累啊，你陪我说会儿话嘛。今天上课的时候老师讲得特别快，"
    "我记笔记都来不及，回来又写了一下午作业。晚上本来想早点睡，"
    "结果又忍不住刷了会儿手机。你那边今天怎么样呀？有没有好好吃饭？"
)


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


def find_reference(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if DEFAULT_REFERENCE.exists():
        return DEFAULT_REFERENCE.resolve()
    if VOICE_DIR.exists():
        audio_exts = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
        for candidate in sorted(VOICE_DIR.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in audio_exts:
                return candidate.resolve()
    return DEFAULT_REFERENCE


def find_transcript(explicit: str | None, reference: Path) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    sibling = reference.with_suffix(".txt")
    if sibling.exists():
        return sibling
    if VOICE_DIR.exists():
        for candidate in sorted(VOICE_DIR.glob("*.txt")):
            return candidate
    return sibling


def read_text_arg(text: str | None, text_file: str | None) -> str:
    if text_file:
        return Path(text_file).expanduser().read_text(encoding="utf-8-sig").strip()
    return (text or "").strip()


def read_transcript(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").strip()
    # The API expects the reference transcript; keep punctuation but collapse
    # blank lines so the payload is stable.
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def audio_data_uri(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        mime = "audio/mpeg"
    elif suffix == ".wav":
        mime = "audio/wav"
    else:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}", mime


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def save_audio(response: httpx.Response, output: Path) -> bool:
    if response.status_code >= 400:
        print(f"HTTP {response.status_code}: {response.text[:800]}", file=sys.stderr)
        return False
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        print(f"返回 JSON 而不是音频：{response.text[:800]}", file=sys.stderr)
        return False
    if not response.content:
        print("返回音频为空。", file=sys.stderr)
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    print(f"已保存：{output}（{output.stat().st_size / 1024:.1f} KiB）")
    return True


def synth_references(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    reference: Path,
    transcript: str,
    text: str,
    output: Path,
    fmt: str,
    sample_rate: int,
    speed: float,
) -> bool:
    data_uri, _ = audio_data_uri(reference)
    payload = {
        "model": model,
        "input": text,
        "voice": "",
        "references": [{"audio": data_uri, "text": transcript}],
        "response_format": fmt,
        "sample_rate": sample_rate,
        "speed": speed,
        "stream": False,
    }
    response = client.post(
        f"{base_url}/audio/speech",
        headers=auth(api_key),
        json=payload,
        timeout=180.0,
    )
    return save_audio(response, output)


def upload_voice(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    reference: Path,
    transcript: str,
    name: str,
) -> str | None:
    _, mime = audio_data_uri(reference)
    with reference.open("rb") as handle:
        response = client.post(
            f"{base_url}/uploads/audio/voice",
            headers=auth(api_key),
            data={"model": model, "customName": name, "text": transcript},
            files={"file": (reference.name, handle, mime)},
            timeout=180.0,
        )
    if response.status_code >= 400:
        print(
            f"上传自定义音色失败 HTTP {response.status_code}: {response.text[:800]}",
            file=sys.stderr,
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        print(f"上传返回不是 JSON：{response.text[:800]}", file=sys.stderr)
        return None
    uri = str(payload.get("uri") or "").strip()
    if not uri:
        print(f"上传返回没有 uri：{payload}", file=sys.stderr)
        return None
    record = {
        "uri": uri,
        "name": name,
        "model": model,
        "reference": str(reference),
        "created_at": int(time.time()),
    }
    meta_path = VOICE_DIR / "siliconflow_voice.json"
    meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        meta_path.chmod(0o600)
    except OSError:
        pass
    print(f"自定义音色已保存：{meta_path}（uri={uri[:28]}…）")
    return uri


def synth_voice(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    voice_uri: str,
    text: str,
    output: Path,
    fmt: str,
    sample_rate: int,
    speed: float,
) -> bool:
    payload = {
        "model": model,
        "input": text,
        "voice": voice_uri,
        "response_format": fmt,
        "sample_rate": sample_rate,
        "speed": speed,
        "stream": False,
    }
    response = client.post(
        f"{base_url}/audio/speech",
        headers=auth(api_key),
        json=payload,
        timeout=180.0,
    )
    return save_audio(response, output)


def main() -> int:
    parser = argparse.ArgumentParser(description="SiliconFlow CosyVoice2 克隆语音测试")
    parser.add_argument("--reference-audio", help="参考音频（默认 private/voice/simple.mp3）")
    parser.add_argument("--reference-text-file", help="参考音频逐字稿（默认同目录同名 .txt）")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要合成的长文本")
    parser.add_argument("--text-file", help="从文件读取要合成的文本（优先于 --text）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--mode",
        choices=["auto", "references", "upload"],
        default="auto",
        help="auto=先 references 再 upload；references=零样本；upload=自定义音色",
    )
    parser.add_argument("--voice-uri", help="已有 SiliconFlow 自定义音色 URI（跳过上传）")
    parser.add_argument("--name", default=f"fatfish-{int(time.time())}", help="自定义音色名")
    parser.add_argument("--model", default=os.getenv("SILICONFLOW_TTS_MODEL", DEFAULT_MODEL))
    parser.add_argument("--format", default="wav", choices=["wav", "mp3"])
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    reference = find_reference(args.reference_audio)
    transcript_path = find_transcript(args.reference_text_file, reference)
    text = read_text_arg(args.text, args.text_file)
    output = Path(args.output).expanduser().resolve()

    if not reference.exists():
        raise SystemExit(f"参考音频不存在：{reference}")
    if not transcript_path.exists():
        raise SystemExit(f"参考文字稿不存在：{transcript_path}")
    if not text:
        raise SystemExit("合成文本为空")
    transcript = read_transcript(transcript_path)

    print(f"参考音频 : {reference}（{reference.stat().st_size / 1024:.1f} KiB）")
    print(f"文字稿   : {transcript_path}（{len(transcript)} 字）")
    print(f"合成文本 : {len(text)} 字")
    print(f"模型     : {args.model}")
    print(f"模式     : {args.mode}")
    print(f"输出     : {output}")

    if args.dry_run:
        encoded, mime = audio_data_uri(reference)
        print(f"MIME     : {mime}")
        print(f"参考音频 base64 长度：{len(encoded)} 字符")
        print("dry-run：校验通过，未调用 API。")
        return 0

    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("没有找到 SILICONFLOW_API_KEY，请先运行 scripts/setup_secrets.py")

    base_url = args.base_url.rstrip("/")
    with httpx.Client() as client:
        if args.voice_uri:
            print(f"复用自定义音色：{args.voice_uri[:28]}…")
            ok = synth_voice(
                client,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                voice_uri=args.voice_uri,
                text=text,
                output=output,
                fmt=args.format,
                sample_rate=args.sample_rate,
                speed=args.speed,
            )
            return 0 if ok else 1

        if args.mode in {"auto", "references"}:
            ok = synth_references(
                client,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                reference=reference,
                transcript=transcript,
                text=text,
                output=output,
                fmt=args.format,
                sample_rate=args.sample_rate,
                speed=args.speed,
            )
            if ok:
                return 0
            if args.mode == "references":
                return 1
            print("references 模式失败，改用 upload 模式。", file=sys.stderr)

        voice_uri = upload_voice(
            client,
            base_url=base_url,
            api_key=api_key,
            model=args.model,
            reference=reference,
            transcript=transcript,
            name=args.name,
        )
        if not voice_uri:
            return 1
        ok = synth_voice(
            client,
            base_url=base_url,
            api_key=api_key,
            model=args.model,
            voice_uri=voice_uri,
            text=text,
            output=output,
            fmt=args.format,
            sample_rate=args.sample_rate,
            speed=args.speed,
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
