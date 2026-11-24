from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import uvicorn

from .runtime import runtime
from .web import app

if __name__ == "__main__":
    settings = runtime.settings
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "fatfish.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
