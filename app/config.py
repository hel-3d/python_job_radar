from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
    telegram_session_name: str = os.getenv("TELEGRAM_SESSION_NAME", "python_job_radar")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    target_channel_id: str = os.getenv("TARGET_CHANNEL_ID", "")
    admin_user_id: int = int(os.getenv("ADMIN_USER_ID", "5521667593"))

    source_channels: list[str] = field(
        default_factory=lambda: _split_csv(os.getenv("SOURCE_CHANNELS", ""))
    )

    min_score_to_queue: int = int(os.getenv("MIN_SCORE_TO_QUEUE", "45"))
    publish_interval_minutes: int = int(os.getenv("PUBLISH_INTERVAL_MINUTES", "30"))
    publish_timezone_offset_hours: int = int(os.getenv("PUBLISH_TZ_OFFSET_HOURS", "4"))
    publish_from_hour: int = int(os.getenv("PUBLISH_FROM_HOUR", "8"))
    publish_to_hour: int = int(os.getenv("PUBLISH_TO_HOUR", "23"))

    queue_dir: Path = BASE_DIR / "data" / "queue"
    photos_dir: Path = BASE_DIR / "data" / "photos"
    cache_file: Path = BASE_DIR / "data" / "posted_posts.txt"
    banned_phrases_file: Path = BASE_DIR / "data" / "banned_phrases.json"
    cut_phrases_file: Path = BASE_DIR / "data" / "cut_phrases.json"
    regex_cut_phrases_file: Path = BASE_DIR / "data" / "regex_cut_phrases.json"
    dynamic_sources_file: Path = BASE_DIR / "data" / "source_channels.json"
    votes_file: Path = BASE_DIR / "data" / "votes.json"
    log_file: Path = BASE_DIR / "logs" / "bot.log"

    def validate_parser(self) -> None:
        missing = []
        if not self.telegram_api_id:
            missing.append("TELEGRAM_API_ID")
        if not self.telegram_api_hash:
            missing.append("TELEGRAM_API_HASH")
        if missing:
            raise RuntimeError("Missing parser settings: " + ", ".join(missing))

    def validate_publisher(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.target_channel_id:
            missing.append("TARGET_CHANNEL_ID")
        if missing:
            raise RuntimeError("Missing publisher settings: " + ", ".join(missing))


settings = Settings()
for directory in [settings.queue_dir, settings.photos_dir, settings.log_file.parent]:
    directory.mkdir(parents=True, exist_ok=True)
