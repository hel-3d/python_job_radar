from __future__ import annotations

import re

from .config import settings
from .storage import load_json_list

IGNORE_PHRASES = [
    "оффтоп", "#реклама", "#резюме", "ищу работу", "подборка каналов",
    "подпишитесь", "бесплатный курс", "день открытых дверей", "волонтер",
    "энтузиазм", "розыгрыш", "курс", "марафон", "интенсив",
]

REMOVE_PHRASES = [
    "Свежие вакансии", "Разместить объявление", "НАЖМИ СЮДА",
    "Больше вакансий", "Подписывайтесь", "Наш канал по игровым вакансиям",
]


def _contains_phrase(text: str, phrases: list[str]) -> bool:
    lower = text.lower()
    return any(phrase.lower() in lower for phrase in phrases if phrase.strip())


def should_ignore(text: str) -> bool:
    dynamic_ignore_phrases = load_json_list(settings.banned_phrases_file)
    return _contains_phrase(text, IGNORE_PHRASES + dynamic_ignore_phrases)


def clean_text(text: str) -> str:
    text = text or ""

    for phrase in REMOVE_PHRASES + load_json_list(settings.cut_phrases_file):
        if phrase.strip():
            text = text.replace(phrase, "")

    for pattern in load_json_list(settings.regex_cut_phrases_file):
        try:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)
        except re.error:
            # Invalid regex rules are ignored so one bad command does not break parsing.
            continue

    # Markdown links: [text](url) -> text: url
    text = re.sub(r"\[([^\]]+)]\((https?://[^\s)]+)\)", r"\1: \2", text)
    # Markdown bold/italic markers
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    # Too many empty lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
