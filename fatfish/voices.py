from __future__ import annotations

import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .db import Database

PROFILE_FIELDS = (
    "name",
    "provider",
    "mode",
    "model",
    "reference_path",
    "reference_text",
    "speed",
    "voice_uri",
    "voice_name",
)


def _row_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["archived"] = bool(data.get("archived"))
    data["active"] = bool(data.get("active"))
    return data


def _read_text(path: Path) -> str:
    try:
        return "\n".join(
            line.strip()
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        )
    except OSError:
        return ""


async def ensure_default_profile(
    db: Database, settings: Settings, user_id: int = 1
) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM voice_profiles WHERE user_id=?",
        (int(user_id),),
    )
    if row and int(row["n"]) > 0:
        return await get_active(db, user_id)

    if settings.siliconflow_api_key:
        reference = settings.siliconflow_tts_reference
        profile = await create_profile(
            db,
            user_id,
            name="当前音色（克隆）",
            provider="siliconflow",
            mode=settings.siliconflow_tts_mode,
            model=settings.siliconflow_tts_model,
            reference_path=str(reference) if reference.exists() else "",
            reference_text=_read_text(settings.siliconflow_tts_reference_text),
            speed=settings.siliconflow_tts_speed,
            voice_name="",
            voice_uri="",
            active=True,
        )
        return profile
    if settings.mimo_api_key:
        return await create_profile(
            db,
            user_id,
            name=f"MiMo 预设（{settings.mimo_tts_voice}）",
            provider="mimo",
            mode="preset",
            model=settings.mimo_tts_model,
            voice_name=settings.mimo_tts_voice,
            active=True,
        )
    return None


async def list_profiles(
    db: Database, user_id: int = 1, include_archived: bool = True
) -> list[dict[str, Any]]:
    where = "" if include_archived else "AND archived=0"
    rows = await db.fetchall(
        f"SELECT * FROM voice_profiles WHERE user_id=? {where} "
        "ORDER BY active DESC, archived ASC, id DESC",
        (int(user_id),),
    )
    return [_row_to_dict(row) for row in rows]  # type: ignore[misc]


async def get_profile(db: Database, profile_id: int) -> dict[str, Any] | None:
    row = await db.fetchone("SELECT * FROM voice_profiles WHERE id=?", (int(profile_id),))
    return _row_to_dict(row)


async def get_active(db: Database, user_id: int = 1) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT * FROM voice_profiles WHERE user_id=? AND active=1 AND archived=0 "
        "ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    if row is not None:
        return _row_to_dict(row)
    row = await db.fetchone(
        "SELECT * FROM voice_profiles WHERE user_id=? AND archived=0 "
        "ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    return _row_to_dict(row)


async def create_profile(
    db: Database, user_id: int = 1, **fields: Any
) -> dict[str, Any]:
    now = time.time()
    user_id = int(user_id)
    values = {
        "name": str(fields.get("name") or "新音色").strip() or "新音色",
        "provider": str(fields.get("provider") or "siliconflow"),
        "mode": str(fields.get("mode") or "references"),
        "model": str(fields.get("model") or ""),
        "reference_path": str(fields.get("reference_path") or ""),
        "reference_text": str(fields.get("reference_text") or ""),
        "speed": float(fields.get("speed") or 0.8),
        "voice_uri": str(fields.get("voice_uri") or ""),
        "voice_name": str(fields.get("voice_name") or ""),
        "archived": 0,
        "active": 1 if fields.get("active") else 0,
        "created_at": now,
        "updated_at": now,
    }
    if values["active"]:
        await db.execute(
            "UPDATE voice_profiles SET active=0 WHERE user_id=?", (user_id,)
        )
    cursor = await db.execute(
        "INSERT INTO voice_profiles(user_id,name,provider,mode,model,reference_path,"
        "reference_text,speed,voice_uri,voice_name,archived,active,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user_id,
            values["name"],
            values["provider"],
            values["mode"],
            values["model"],
            values["reference_path"],
            values["reference_text"],
            values["speed"],
            values["voice_uri"],
            values["voice_name"],
            values["archived"],
            values["active"],
            values["created_at"],
            values["updated_at"],
        ),
    )
    profile = await get_profile(db, int(cursor.lastrowid or 0))
    assert profile is not None
    return profile


async def update_profile(
    db: Database, profile_id: int, **fields: Any
) -> dict[str, Any]:
    profile = await get_profile(db, profile_id)
    if profile is None:
        raise KeyError("音色档案不存在")
    updates: dict[str, Any] = {}
    for key in PROFILE_FIELDS:
        if key in fields and fields[key] is not None:
            updates[key] = fields[key]
    if "speed" in updates:
        updates["speed"] = float(updates["speed"])
    if not updates:
        return profile
    updates["updated_at"] = time.time()
    assignments = ",".join(f"{key}=?" for key in updates)
    await db.execute(
        f"UPDATE voice_profiles SET {assignments} WHERE id=?",
        (*updates.values(), int(profile_id)),
    )
    updated = await get_profile(db, profile_id)
    assert updated is not None
    return updated


async def set_active(db: Database, profile_id: int) -> dict[str, Any]:
    profile = await get_profile(db, profile_id)
    if profile is None:
        raise KeyError("音色档案不存在")
    if profile["archived"]:
        raise ValueError("已归档的音色不能设为当前音色")
    user_id = int(profile["user_id"])
    await db.execute(
        "UPDATE voice_profiles SET active=0 WHERE user_id=?", (user_id,)
    )
    await db.execute(
        "UPDATE voice_profiles SET active=1, updated_at=? WHERE id=?",
        (time.time(), int(profile_id)),
    )
    updated = await get_profile(db, profile_id)
    assert updated is not None
    return updated


async def archive_profile(db: Database, profile_id: int) -> dict[str, Any]:
    profile = await get_profile(db, profile_id)
    if profile is None:
        raise KeyError("音色档案不存在")
    user_id = int(profile["user_id"])
    await db.execute(
        "UPDATE voice_profiles SET archived=1, active=0, updated_at=? WHERE id=?",
        (time.time(), int(profile_id)),
    )
    if profile["active"]:
        replacement = await db.fetchone(
            "SELECT id FROM voice_profiles WHERE user_id=? AND archived=0 "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        if replacement is not None:
            await db.execute(
                "UPDATE voice_profiles SET active=1 WHERE id=?",
                (int(replacement["id"]),),
            )
    updated = await get_profile(db, profile_id)
    assert updated is not None
    return updated


async def delete_profile(db: Database, profile_id: int) -> dict[str, Any]:
    profile = await get_profile(db, profile_id)
    if profile is None:
        raise KeyError("音色档案不存在")
    if profile["active"]:
        raise ValueError("不能删除当前音色，请先切换或归档")
    await db.execute("DELETE FROM voice_profiles WHERE id=?", (int(profile_id),))
    return profile


def store_reference(settings: Settings, data: bytes, suffix: str) -> Path:
    suffix = suffix if suffix else ".mp3"
    directory = settings.data_dir / "voice" / "profiles" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"reference{suffix}"
    target.write_bytes(data)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


async def clone_breeze_voice(
    settings: Settings, data: bytes, filename: str, name: str
) -> str:
    """Clone a sample into a saved Breeze Blue voice and return its voice_id."""
    if not settings.breeze_api_key:
        raise RuntimeError("没有 BREEZE_API_KEY")
    suffix = Path(filename or "").suffix.lower() or ".wav"
    mime = mimetypes.guess_type(f"sample{suffix}")[0] or "audio/wav"
    base_url = settings.breeze_base_url.rstrip("/")
    headers = {"xi-api-key": settings.breeze_api_key}
    voice_name = (name or "Breeze 克隆音色").strip()[:64]
    async with httpx.AsyncClient(timeout=240.0) as client:
        preview = await client.post(
            f"{base_url}/v1/voice-previews/clone",
            headers=headers,
            data={
                "name": voice_name,
                "reference_language_code": settings.breeze_language_code,
            },
            files=[("files", (Path(filename or "sample.wav").name, data, mime))],
        )
        if preview.status_code >= 400:
            raise RuntimeError(
                f"Breeze 克隆预览失败 HTTP {preview.status_code}: {preview.text[:400]}"
            )
        try:
            preview_payload = preview.json()
        except ValueError as exc:
            raise RuntimeError("Breeze 克隆预览返回不是 JSON") from exc
        generated_id = str(
            (preview_payload or {}).get("generated_voice_id") or ""
        ).strip()
        if not generated_id:
            raise RuntimeError("Breeze 克隆预览缺少 generated_voice_id")

        saved = await client.post(
            f"{base_url}/v1/voice-previews/{generated_id}/save",
            headers=headers,
            json={
                "voice_name": voice_name,
                "language_code": settings.breeze_language_code,
            },
        )
        if saved.status_code >= 400:
            raise RuntimeError(
                f"Breeze 保存音色失败 HTTP {saved.status_code}: {saved.text[:400]}"
            )
        try:
            saved_payload = saved.json()
        except ValueError as exc:
            raise RuntimeError("Breeze 保存音色返回不是 JSON") from exc
        voice_id = str(
            (saved_payload or {}).get("voice_id")
            or (saved_payload or {}).get("id")
            or ""
        ).strip()
        if not voice_id:
            raise RuntimeError("Breeze 保存音色缺少 voice_id")
        return voice_id


async def upload_voice_to_provider(
    settings: Settings, profile: dict[str, Any]
) -> str:
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("没有 SILICONFLOW_API_KEY")
    reference = Path(str(profile.get("reference_path") or ""))
    if not reference.is_file():
        raise RuntimeError("参考音频不存在")
    transcript = str(profile.get("reference_text") or "").strip()
    if not transcript:
        raise RuntimeError("上传需要参考音频的逐字稿")
    mime = mimetypes.guess_type(reference.name)[0] or "audio/mpeg"
    model = str(profile.get("model") or settings.siliconflow_tts_model)
    name = str(profile.get("name") or f"fatfish-{int(time.time())}")[:64]
    with reference.open("rb") as handle:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{settings.siliconflow_base_url}/uploads/audio/voice",
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model, "customName": name, "text": transcript},
                files={"file": (reference.name, handle, mime)},
            )
    if response.status_code >= 400:
        raise RuntimeError(
            f"上传音色失败 HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("上传返回不是 JSON") from exc
    uri = str(payload.get("uri") or "").strip()
    if not uri:
        raise RuntimeError("上传返回缺少 uri")
    return uri
