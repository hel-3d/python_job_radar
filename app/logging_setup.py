from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import settings


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(
                settings.log_file,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )