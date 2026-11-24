from __future__ import annotations

import json
import os
import tempfile

from .config import SETTINGS_PATH, load_overlay


def mask_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= 8:
        return "••••"
    return "…" + raw[-4:]


def save_overrides(updates: dict[str, str]) -> dict[str, str]:
    overlay = load_overlay()
    overlay.update({str(key): str(value) for key, value in updates.items()})
    _write(overlay)
    return overlay


def clear_overrides(keys: list[str]) -> dict[str, str]:
    overlay = load_overlay()
    for key in keys:
        overlay.pop(key, None)
    _write(overlay)
    return overlay


def _write(overlay: dict[str, str]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(SETTINGS_PATH.parent), prefix=".settings-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(overlay, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, SETTINGS_PATH)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass
