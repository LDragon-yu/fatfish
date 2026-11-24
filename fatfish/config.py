from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "private" / "settings.json"


def load_overlay() -> dict[str, str]:
    """UI-managed overrides stored in private/settings.json.

    Values here take precedence over .env. Missing keys fall back to .env.
    """
    try:
        raw = SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def _bool_value(raw: str, default: bool) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    root: Path
    data_dir: Path
    db_path: Path
    host: str
    port: int
    timezone: str
    bot_name: str
    user_name: str

    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    vision_model: str
    llm_temperature: float
    llm_max_tokens: int
    mock_llm: bool

    recent_messages: int
    max_context_chars: int
    max_tool_rounds: int
    max_burst_messages: int
    scheduler_interval_seconds: int
    min_think_minutes: int
    max_think_minutes: int
    proactive_enabled: bool
    log_level: str

    ffmpeg_path: str
    owner_qq: str
    onebot_access_token: str
    onebot_enabled: bool
    tts_max_chars: int
    voice_probability: float

    siliconflow_api_key: str
    siliconflow_base_url: str
    siliconflow_asr_model: str
    siliconflow_tts_model: str
    siliconflow_tts_mode: str
    siliconflow_tts_reference: Path
    siliconflow_tts_reference_text: Path
    siliconflow_tts_speed: float
    siliconflow_voice_file: Path

    mimo_api_key: str
    mimo_base_url: str
    mimo_tts_model: str
    mimo_tts_voice: str
    mimo_clone_model: str
    mimo_voice_sample: Path

    breeze_api_key: str
    breeze_base_url: str
    breeze_tts_model: str
    breeze_language_code: str

    split_messages: bool
    split_delay_seconds: float
    split_max_segments: int

    media_enabled: bool
    media_describe: bool
    image_max_side: int
    image_max_mb: int
    video_fps: float
    video_max_frames: int
    video_max_mb: int

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        overlay = load_overlay()

        def env(name: str, default: str = "") -> str:
            if name in overlay:
                return overlay[name]
            return os.getenv(name, default)

        def env_bool(name: str, default: bool) -> bool:
            raw = env(name, "").strip()
            return _bool_value(raw, default)

        def env_int(name: str, default: int) -> int:
            try:
                return int(float(env(name, str(default)).strip() or default))
            except (TypeError, ValueError):
                return default

        def env_float(name: str, default: float) -> float:
            try:
                return float(env(name, str(default)).strip() or default)
            except (TypeError, ValueError):
                return default

        def env_path(name: str, default: Path) -> Path:
            raw = env(name, "").strip()
            return Path(raw).expanduser().resolve() if raw else default

        data_dir = env_path("FATFISH_DATA_DIR", ROOT / "private")
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(data_dir, 0o700)
        except OSError:
            pass

        db_path = env_path("FATFISH_DB_PATH", data_dir / "fatfish.db")
        api_key = env("DEEPSEEK_API_KEY", "").strip()
        deepseek_model = env("DEEPSEEK_MODEL", "deepseek-flash").strip()
        vision_model = env("FATFISH_VISION_MODEL", deepseek_model).strip() or deepseek_model

        return cls(
            root=ROOT,
            data_dir=data_dir,
            db_path=db_path,
            host=env("FATFISH_HOST", "127.0.0.1").strip(),
            port=env_int("FATFISH_PORT", 8080),
            timezone=env("FATFISH_TZ", "Asia/Shanghai").strip(),
            bot_name=env("FATFISH_BOT_NAME", "茉莉").strip(),
            user_name=env("FATFISH_USER_NAME", "你").strip(),
            deepseek_api_key=api_key,
            deepseek_base_url=env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=deepseek_model,
            vision_model=vision_model,
            llm_temperature=env_float("FATFISH_TEMPERATURE", 0.8),
            llm_max_tokens=env_int("FATFISH_MAX_TOKENS", 900),
            mock_llm=env_bool("FATFISH_MOCK_LLM", not bool(api_key)),
            recent_messages=env_int("FATFISH_RECENT_MESSAGES", 30),
            max_context_chars=env_int("FATFISH_MAX_CONTEXT_CHARS", 12000),
            max_tool_rounds=env_int("FATFISH_MAX_TOOL_ROUNDS", 6),
            max_burst_messages=env_int("FATFISH_MAX_BURST", 20),
            scheduler_interval_seconds=env_int("FATFISH_SCHEDULER_INTERVAL", 30),
            min_think_minutes=env_int("FATFISH_MIN_THINK_MINUTES", 5),
            max_think_minutes=env_int("FATFISH_MAX_THINK_MINUTES", 720),
            proactive_enabled=env_bool("FATFISH_PROACTIVE", True),
            log_level=env("FATFISH_LOG_LEVEL", "INFO").strip().upper(),
            ffmpeg_path=env("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg",
            owner_qq=env("OWNER_QQ", "").strip(),
            onebot_access_token=env("ONEBOT_ACCESS_TOKEN", "").strip(),
            onebot_enabled=env_bool("ONEBOT_ENABLED", True),
            tts_max_chars=env_int("TTS_MAX_CHARS", 300),
            voice_probability=max(
                0.0,
                min(1.0, env_float("FATFISH_VOICE_PROBABILITY", 0.2)),
            ),
            siliconflow_api_key=env("SILICONFLOW_API_KEY", "").strip(),
            siliconflow_base_url=env(
                "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
            ).rstrip("/"),
            siliconflow_asr_model=env(
                "SILICONFLOW_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall"
            ).strip(),
            siliconflow_tts_model=env(
                "SILICONFLOW_TTS_MODEL", "FunAudioLLM/CosyVoice2-0.5B"
            ).strip(),
            siliconflow_tts_mode=env("SILICONFLOW_TTS_MODE", "references").strip(),
            siliconflow_tts_reference=env_path(
                "SILICONFLOW_TTS_REFERENCE", data_dir / "voice" / "simple.mp3"
            ),
            siliconflow_tts_reference_text=env_path(
                "SILICONFLOW_TTS_REFERENCE_TEXT", data_dir / "voice" / "simple.txt"
            ),
            siliconflow_tts_speed=env_float("SILICONFLOW_TTS_SPEED", 0.8),
            siliconflow_voice_file=env_path(
                "SILICONFLOW_VOICE_FILE", data_dir / "voice" / "siliconflow_voice.json"
            ),
            mimo_api_key=env("MIMO_API_KEY", "").strip(),
            mimo_base_url=env(
                "MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"
            ).rstrip("/"),
            mimo_tts_model=env("MIMO_TTS_MODEL", "mimo-v2.5-tts").strip(),
            mimo_tts_voice=env("MIMO_TTS_VOICE", "茉莉").strip(),
            mimo_clone_model=env("MIMO_CLONE_MODEL", "mimo-v2.5-tts-voiceclone").strip(),
            mimo_voice_sample=env_path(
                "MIMO_VOICE_SAMPLE", data_dir / "voice" / "simple.mp3"
            ),
            breeze_api_key=env("BREEZE_API_KEY", "").strip(),
            breeze_base_url=env(
                "BREEZE_BASE_URL", "https://api.breeze.blue"
            ).strip().rstrip("/"),
            breeze_tts_model=env("BREEZE_TTS_MODEL", "breeze-tts-2").strip(),
            breeze_language_code=env("BREEZE_LANGUAGE_CODE", "zh").strip() or "zh",
            split_messages=env_bool("FATFISH_SPLIT_MESSAGES", True),
            split_delay_seconds=env_float("FATFISH_SPLIT_DELAY", 0.35),
            split_max_segments=env_int("FATFISH_SPLIT_MAX_SEGMENTS", 20),
            media_enabled=env_bool("FATFISH_MEDIA_ENABLED", True),
            media_describe=env_bool("FATFISH_MEDIA_DESCRIBE", True),
            image_max_side=env_int("FATFISH_IMAGE_MAX_SIDE", 1600),
            image_max_mb=env_int("FATFISH_IMAGE_MAX_MB", 10),
            video_fps=env_float("FATFISH_VIDEO_FPS", 1.0),
            video_max_frames=env_int("FATFISH_VIDEO_MAX_FRAMES", 30),
            video_max_mb=env_int("FATFISH_VIDEO_MAX_MB", 80),
        )

    @classmethod
    def for_test(cls, data_dir: Path) -> "Settings":
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=ROOT,
            data_dir=data_dir,
            db_path=data_dir / "test.db",
            host="127.0.0.1",
            port=0,
            timezone="Asia/Shanghai",
            bot_name="茉莉",
            user_name="你",
            deepseek_api_key="",
            deepseek_base_url="",
            deepseek_model="mock",
            vision_model="mock",
            llm_temperature=0.0,
            llm_max_tokens=400,
            mock_llm=True,
            recent_messages=12,
            max_context_chars=6000,
            max_tool_rounds=4,
            max_burst_messages=5,
            scheduler_interval_seconds=1,
            min_think_minutes=1,
            max_think_minutes=60,
            proactive_enabled=False,
            log_level="DEBUG",
            ffmpeg_path="ffmpeg",
            owner_qq="",
            onebot_access_token="",
            onebot_enabled=False,
            tts_max_chars=300,
            voice_probability=0.0,
            siliconflow_api_key="",
            siliconflow_base_url="https://api.siliconflow.cn/v1",
            siliconflow_asr_model="FunAudioLLM/SenseVoiceSmall",
            siliconflow_tts_model="FunAudioLLM/CosyVoice2-0.5B",
            siliconflow_tts_mode="references",
            siliconflow_tts_reference=data_dir / "voice" / "simple.mp3",
            siliconflow_tts_reference_text=data_dir / "voice" / "simple.txt",
            siliconflow_tts_speed=0.8,
            siliconflow_voice_file=data_dir / "voice" / "siliconflow_voice.json",
            mimo_api_key="",
            mimo_base_url="https://api.xiaomimimo.com/v1",
            mimo_tts_model="mimo-v2.5-tts",
            mimo_tts_voice="茉莉",
            mimo_clone_model="mimo-v2.5-tts-voiceclone",
            mimo_voice_sample=data_dir / "voice" / "simple.mp3",
            breeze_api_key="",
            breeze_base_url="https://api.breeze.blue",
            breeze_tts_model="breeze-tts-2",
            breeze_language_code="zh",
            split_messages=True,
            split_delay_seconds=0.35,
            split_max_segments=20,
            media_enabled=True,
            media_describe=True,
            image_max_side=1600,
            image_max_mb=10,
            video_fps=1.0,
            video_max_frames=30,
            video_max_mb=80,
        )
