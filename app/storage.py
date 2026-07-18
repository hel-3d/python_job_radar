from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .config import settings

MAX_CACHE_POSTS = 500


def load_post_cache() -> list[str]:
    if settings.cache_file.exists():
        return [line.strip() for line in settings.cache_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    return []


def save_post_cache(items: list[str]) -> None:
    settings.cache_file.write_text("\n".join(item.replace("\n", " ") for item in items[-MAX_CACHE_POSTS:]), encoding="utf-8")


def is_duplicate(text: str, cached_posts: list[str], threshold: int = 90) -> bool:
    normalized = text.replace("\n", " ").strip()
    return any(fuzz.ratio(normalized, saved) >= threshold for saved in cached_posts)


def load_json_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def save_json_list(path: Path, items: list[str]) -> None:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        key = value.lower()
        if value and key not in seen:
            normalized.append(value)
            seen.add(key)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def add_unique_item(path: Path, item: str) -> tuple[bool, list[str]]:
    items = load_json_list(path)
    value = item.strip()
    if not value:
        return False, items
    if value.lower() in {existing.lower() for existing in items}:
        return False, items
    items.append(value)
    save_json_list(path, items)
    return True, items


def remove_item_by_number(path: Path, number: int) -> tuple[str | None, list[str]]:
    items = load_json_list(path)
    index = number - 1
    if index < 0 or index >= len(items):
        return None, items
    removed = items.pop(index)
    save_json_list(path, items)
    return removed, items


def save_to_queue(
    text: str,
    score: int,
    source_channel: str,
    photo_path: str | None = None,
    source: str = "telegram",
    source_name: str | None = None,
    url: str = "",
    posted_at_text: str = "",
    title: str = "",
    description: str = "",
    questions: list[str] | None = None,
    questions_count: int | None = None,
    has_questions: bool | None = None,
    skills: list[str] | None = None,
    budget: dict[str, Any] | None = None,
    workload: str = "",
    duration: str = "",
    experience_level: str = "",
    contract_to_hire: bool | None = None,
    project_type: str = "",
    competition: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    client_history: list[dict[str, Any]] | None = None,
    client_name_guess: str = "",
    preferred_qualifications: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Save a job to JSON queue.

    All Upwork-specific fields are optional, so old Telegram parser calls remain valid.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = settings.queue_dir / f"{timestamp}_{score}.json"

    normalized_questions = questions or []
    normalized_skills = skills or []

    data: dict[str, Any] = {
        "source": source,
        "source_channel": source_channel,
        "source_name": source_name or source_channel,
        "url": url,
        "title": title,
        "posted_at_text": posted_at_text,
        "description": description,
        "questions": normalized_questions,
        "questions_count": questions_count if questions_count is not None else len(normalized_questions),
        "has_questions": has_questions if has_questions is not None else bool(normalized_questions),
        "skills": normalized_skills,
        "budget": budget or {},
        "workload": workload,
        "duration": duration,
        "experience_level": experience_level,
        "contract_to_hire": bool(contract_to_hire) if contract_to_hire is not None else False,
        "project_type": project_type,
        "competition": competition or {},
        "client": client or {},
        "client_history": client_history or [],
        "preferred_qualifications": preferred_qualifications or {},
        "client_name_guess": client_name_guess,
        "text": text,
        "raw_text": text,
        "clean_text": text,
        "score": score,
        "pre_score": score,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    if photo_path:
        data["photo"] = photo_path

    if extra:
        data["extra"] = extra

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
