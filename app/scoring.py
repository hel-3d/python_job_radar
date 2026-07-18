from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ScoreResult:
    score: int
    level: str
    found: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    salary: str | None = None
    location: str | None = None
    stack: list[str] = field(default_factory=list)

    def header(self) -> str:
        parts = [f"⭐ Match: {self.score}% ({self.level})"]
        if self.salary:
            parts.append(f"💰 Salary: {self.salary}")
        if self.location:
            parts.append(f"🌍 Format: {self.location}")
        if self.stack:
            parts.append("🧠 Stack: " + ", ".join(self.stack[:10]))
        if self.found:
            parts.append("✅ Fits: " + ", ".join(self.found[:8]))
        if self.red_flags:
            parts.append("⚠️ Red flags: " + ", ".join(self.red_flags[:6]))
        return "\n".join(parts)


POSITIVE_KEYWORDS: dict[str, int] = {
    # Core
    "python": 30,
    "backend": 18,
    "api": 18,
    "rest": 10,
    "rest api": 15,

    # Web
    "fastapi": 22,
    "django": 15,
    "flask": 12,

    # Automation
    "automation": 20,
    "автоматизация": 20,
    "selenium": 22,
    "playwright": 22,
    "scraping": 22,
    "парсинг": 22,

    # Telegram
    "telegram": 18,
    "telegram bot": 25,
    "telegram api": 20,
    "aiogram": 25,
    "telethon": 25,

    # Databases
    "postgresql": 15,
    "postgres": 15,
    "sql": 15,
    "sqlite": 8,
    "mongodb": 12,
    "redis": 12,

    # Infra
    "docker": 15,
    "linux": 12,
    "nginx": 8,
    "vps": 10,
    "git": 8,

    # Async / integrations
    "asyncio": 12,
    "webhook": 10,
    "integration": 15,
    "интеграция": 15,
    "json": 8,

    # Data
    "etl": 15,
    "pandas": 12,
    "polars": 12,
    "data analyst": 10,
    "data engineer": 15,
    "analytics": 8,
    "аналитика": 8,

    # AI / LLM
    "ai": 15,
    "llm": 20,
    "openai": 18,
    "anthropic": 15,
    "claude": 15,
    "rag": 22,
    "langchain": 20,
    "langgraph": 25,
    "vector database": 12,
    "qdrant": 18,
    "mcp": 18,
    "agent": 15,
    "ai agent": 20,

    # QA / Testing
    "pytest": 18,
    "qa automation": 18,
    "автотест": 15,
    "тестирование api": 15,

    # Marketplace / integrations
    "google sheets": 15,
    "wildberries": 12,
    "ozon": 12,
    "yandex market": 12,

    # Messaging / bots
    "discord": 8,
    "slack": 8,

    # Security
    "sentinel": 20,
    "microsoft sentinel": 25,
    "xdr": 15,
    "threat hunting": 20,
}

GOOD_FORMAT: dict[str, int] = {
    "remote": 12,
    "удален": 12,
    "удалён": 12,
    "релокация не требуется": 8,
    "гибкий график": 8,
    "part-time": 8,
    "частичная занятость": 8,
    "async": 8,
    "асинхрон": 8,
    "usdt": 5,
    "usd": 5,
}

NEGATIVE_KEYWORDS: dict[str, int] = {
    # Не твой стек
    "java": -20,
    "spring": -20,
    "php": -20,
    "laravel": -20,
    "c++": -15,
    "c#": -12,
    ".net": -12,
    "golang": -8,
    "go developer": -8,

    # Мобильная разработка
    "ios": -20,
    "android": -20,
    "flutter": -20,
    "react native": -20,

    # Чистый фронт
    "frontend": -15,
    "front-end": -15,
    "react": -8,
    "vue": -8,
    "angular": -8,

    # Железо / embedded
    "embedded": -20,
    "firmware": -20,
    "microcontroller": -20,
    "arduino": -15,

    # Офис
    "only office": -30,
    "только офис": -30,
    "офис": -8,
    "офис москва": -25,
    "офис спб": -20,
    "релокация в москву": -25,

    # Английский
    "fluent english": -25,
    "advanced english": -20,
    "c1 english": -20,
    "c2 english": -25,
    "свободный английский": -20,
    "устный английский": -15,
    "разговорный английский": -15,
    "customer-facing": -15,

    # Опыт
    "7+ лет": -25,
    "8+ лет": -30,
    "10+ лет": -40,

    # Продажи
    "холодные звонки": -50,
    "активные продажи": -50,
    "менеджер по продажам": -50,

    # Для тебя обычно плохой знак
    "работа в офисе": -20,
    "полная занятость в офисе": -25,

    # Нецелевые направления
    "1с": -30,
    "bitrix разработчик": -25,
    "wordpress": -25,
    "tilda": -30,
}

STACK_PATTERNS = [
    "Python", "FastAPI", "Django", "Flask", "Selenium", "Playwright", "BeautifulSoup",
    "Scrapy", "PostgreSQL", "SQLite", "MongoDB", "Docker", "Linux", "Redis",
    "Celery", "RabbitMQ", "Kafka", "aiogram", "Telethon", "OpenAI", "LangChain", "LangGraph",
]


def _contains(text: str, keyword: str) -> bool:
    if re.fullmatch(r"[a-zA-Z0-9_+#. -]+", keyword):
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, flags=re.IGNORECASE) is not None
    return keyword.lower() in text.lower()


def extract_salary(text: str) -> str | None:
    patterns = [
        r"\$\s?\d[\d\s]*(?:\s?[-–—]\s?\$?\s?\d[\d\s]*)?",
        r"\d[\d\s]*(?:\s?[-–—]\s?\d[\d\s]*)?\s?(?:usd|eur|€|\$|usdt)",
        r"\d[\d\s]*(?:\s?[-–—]\s?\d[\d\s]*)?\s?(?:руб|₽|rub)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def detect_location(text: str) -> str | None:
    lower = text.lower()
    if any(word in lower for word in ["remote", "удален", "удалён"]):
        return "Remote"
    if "hybrid" in lower or "гибрид" in lower:
        return "Hybrid"
    if "office" in lower or "офис" in lower:
        return "Office"
    return None


def score_job(text: str) -> ScoreResult:
    score = 0
    found: list[str] = []
    red_flags: list[str] = []

    for keyword, points in POSITIVE_KEYWORDS.items():
        if _contains(text, keyword):
            score += points
            found.append(keyword)

    for keyword, points in GOOD_FORMAT.items():
        if _contains(text, keyword):
            score += points
            found.append(keyword)

    for keyword, points in NEGATIVE_KEYWORDS.items():
        if _contains(text, keyword):
            score += points
            red_flags.append(keyword)

    salary = extract_salary(text)
    if salary:
        score += 8

    stack = [name for name in STACK_PATTERNS if _contains(text, name)]
    location = detect_location(text)

    # Вакансии без Python, но с близкими задачами, не отбрасываем совсем.
    if "python" not in text.lower() and any(item in found for item in ["scraping", "парсинг", "automation", "автоматизация", "api"]):
        score += 10
        found.append("Python-adjacent tasks")

    score = max(0, min(100, score))
    if score >= 80:
        level = "strong"
    elif score >= 60:
        level = "good"
    elif score >= 45:
        level = "maybe"
    else:
        level = "weak"

    return ScoreResult(score=score, level=level, found=found, red_flags=red_flags, salary=salary, location=location, stack=stack)
