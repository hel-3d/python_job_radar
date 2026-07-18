from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from openai import OpenAI

from app.config import BASE_DIR, settings
from app.logging_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

PROMPTS_DIR = BASE_DIR / "app" / "prompts"

# Пока используем старую очередь как inbox.
INBOX_DIR = settings.queue_dir

APPROVED_DIR = BASE_DIR / "data" / "approved"
REJECTED_DIR = BASE_DIR / "data" / "rejected"
ERRORS_DIR = BASE_DIR / "data" / "errors"
PROCESSING_DIR = BASE_DIR / "data" / "processing"

RESUME_URL = "https://hel-3d.github.io/hel-3d/1.1_Resume.html"
GITHUB_URL = "https://github.com/hel-3d"


def ensure_dirs() -> None:
    for directory in [INBOX_DIR, APPROVED_DIR, REJECTED_DIR, ERRORS_DIR, PROCESSING_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_prompts() -> tuple[str, str, str]:
    system_prompt = read_text(PROMPTS_DIR / "system_prompt.txt")
    user_prompt_template = read_text(PROMPTS_DIR / "user_prompt.txt")
    candidate_profile = read_text(PROMPTS_DIR / "candidate_profile.txt")
    return system_prompt, user_prompt_template, candidate_profile


def ensure_links(text: str) -> str:
    text = text.strip()

    if RESUME_URL not in text:
        text += f"\n\nResume:\n{RESUME_URL}"

    if GITHUB_URL not in text:
        text += f"\n\nGitHub:\n{GITHUB_URL}"

    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json\n", "", 1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def build_structured_job_text(job: dict[str, Any]) -> str:
    """Build a clean text block for GPT.

    For old Telegram jobs this falls back to text/clean_text/raw_text.
    For Upwork jobs it uses separated fields and avoids duplicated description.
    """
    if job.get("source") != "upwork":
        return job.get("text") or job.get("clean_text") or job.get("raw_text") or ""

    parts: list[str] = []

    for label, key in [
        ("Title", "title"),
        ("Posted", "posted_at_text"),
        ("Needs to hire", "needs_to_hire"),
        ("Workload", "workload"),
        ("Duration", "duration"),
        ("Experience level", "experience_level"),
        ("Project type", "project_type"),
    ]:
        value = job.get(key)
        if value:
            parts.append(f"{label}: {value}")

    description = job.get("description") or ""
    if description:
        parts.append("Description:\n" + str(description))
    else:
        fallback = job.get("text") or job.get("clean_text") or job.get("raw_text") or ""
        if fallback:
            parts.append(str(fallback))

    skills = job.get("skills") or []
    if skills:
        parts.append("Skills: " + ", ".join(str(item) for item in skills))

    questions = job.get("questions") or []
    if questions:
        question_lines = [f"{index}. {question}" for index, question in enumerate(questions, start=1)]
        parts.append("Proposal questions:\n" + "\n".join(question_lines))
    else:
        parts.append("Proposal questions: none")

    for label, key in [
        ("Budget", "budget"),
        ("Competition", "competition"),
        ("Preferred qualifications", "preferred_qualifications"),
        ("Client", "client"),
        ("Client recent history", "client_history"),
        ("Other open jobs by client", "other_open_jobs"),
    ]:
        value = job.get(key)
        if value:
            parts.append(f"{label}: {json.dumps(value, ensure_ascii=False)}")

    client_name_guess = job.get("client_name_guess") or ""
    if client_name_guess:
        parts.append(f"Possible client name from reviews: {client_name_guess}")

    extra = job.get("extra") or {}
    if isinstance(extra, dict) and extra:
        parts.append("Extra parsed data: " + json.dumps(extra, ensure_ascii=False))

    url = job.get("url") or ""
    if url:
        parts.append("URL: " + str(url))

    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())

def build_user_prompt(
    template: str,
    candidate_profile: str,
    job: dict[str, Any],
) -> str:
    job_text = build_structured_job_text(job)
    source_channel = job.get("source_channel") or job.get("source_name") or job.get("source") or "unknown"
    job_url = job.get("url") or ""
    pre_score = str(job.get("score") or job.get("pre_score") or "")

    prompt = template
    prompt = prompt.replace("{{job_text}}", job_text)
    prompt = prompt.replace("{{source_channel}}", str(source_channel))
    prompt = prompt.replace("{{job_url}}", str(job_url))
    prompt = prompt.replace("{{pre_score}}", pre_score)

    if job.get("questions"):
        prompt += (
            "\n\nВажно: в вакансии есть отдельные вопросы для proposal. "
            "Если вакансия рекомендована, учитывай их при сопроводительном письме. "
            "Сами вопросы сохранены отдельным массивом questions в JSON вакансии."
        )

    return (
        f"{prompt}\n\n"
        f"Дополнительный профиль кандидата:\n"
        f"{candidate_profile}"
    )


def review_job(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = response.choices[0].message.content or "{}"
    result = extract_json(content)

    if result.get("cover_letter_ru"):
        result["cover_letter_ru"] = ensure_links(str(result["cover_letter_ru"]))

    if result.get("cover_letter_en"):
        result["cover_letter_en"] = ensure_links(str(result["cover_letter_en"]))

    return result

def money_range(budget: dict[str, Any]) -> str:
    if not budget:
        return ""

    budget_type = str(budget.get("type") or "").strip()
    budget_min = budget.get("min")
    budget_max = budget.get("max")

    if budget_min is not None and budget_max is not None:
        return f"${budget_min:g}–${budget_max:g} {budget_type}".strip()

    if budget_min is not None:
        return f"${budget_min:g} {budget_type}".strip()

    return ""


def format_client_brief(client: dict[str, Any]) -> str:
    if not client:
        return "нет данных"

    parts = []

    if client.get("payment_verified"):
        parts.append("оплата подтверждена")
    elif client.get("payment_not_verified"):
        parts.append("оплата НЕ подтверждена")

    if client.get("rating") is not None:
        parts.append(f"рейтинг {client.get('rating')}/5")

    if client.get("reviews_count") is not None:
        parts.append(f"отзывов: {client.get('reviews_count')}")

    if client.get("country"):
        parts.append(f"страна: {client.get('country')}")

    if client.get("jobs_posted") is not None:
        parts.append(f"вакансий: {client.get('jobs_posted')}")

    if client.get("hire_rate") is not None:
        parts.append(f"hire rate: {client.get('hire_rate')}%")

    if client.get("total_spent"):
        parts.append(f"потрачено: {client.get('total_spent')}")

    if client.get("hires") is not None:
        parts.append(f"наймов: {client.get('hires')}")

    if client.get("member_since"):
        parts.append(str(client.get("member_since")))

    return "; ".join(parts)


def format_competition_brief(competition: dict[str, Any]) -> str:
    if not competition:
        return "нет данных"

    parts = []

    if competition.get("proposals"):
        parts.append(f"отклики: {competition.get('proposals')}")

    if competition.get("interviewing") is not None:
        parts.append(f"интервью: {competition.get('interviewing')}")

    if competition.get("invites_sent") is not None:
        parts.append(f"инвайты: {competition.get('invites_sent')}")

    if competition.get("last_viewed_by_client"):
        parts.append(f"клиент смотрел: {competition.get('last_viewed_by_client')}")

    return "; ".join(parts)


def format_history_brief(history: list[Any]) -> str:
    if not history:
        return "нет данных"

    lines = []

    for item in history[:5]:
        if not isinstance(item, dict):
            continue

        title = item.get("title") or "проект"
        amount = item.get("amount_text") or ""
        hours = item.get("hours")
        rate = item.get("hourly_rate")
        client_rating = item.get("client_rating")
        freelancer_rating = item.get("freelancer_rating")
        client_review = item.get("client_review") or ""
        freelancer_review = item.get("freelancer_review") or ""

        line = f"• {title}"

        details = []
        if amount:
            details.append(str(amount))
        elif hours and rate:
            details.append(f"{hours:g} ч @ ${rate:g}/hr")

        if client_rating is not None:
            details.append(f"оценка клиента: {client_rating}/5")

        if freelancer_rating is not None:
            details.append(f"оценка фрилансера: {freelancer_rating}/5")

        if details:
            line += " — " + "; ".join(details)

        if client_review:
            line += f"\n  отзыв фрилансера о клиенте: {client_review}"

        if freelancer_review:
            line += f"\n  отзыв клиента о фрилансере: {freelancer_review}"

        lines.append(line)

    return "\n".join(lines) if lines else "нет данных"


def format_upwork_summary(job: dict[str, Any]) -> str:
    budget = money_range(job.get("budget") or {})
    skills = job.get("skills") or []
    preferred = job.get("preferred_qualifications") or {}

    lines = [
        f"📌 Название: {job.get('title') or 'без названия'}",
        f"🔗 Ссылка: {job.get('url') or 'нет'}",
    ]

    if job.get("posted_at_text"):
        lines.append(f"🕒 Опубликовано: {job.get('posted_at_text')}")

    if budget:
        lines.append(f"💰 Бюджет: {budget}")

    if job.get("workload"):
        lines.append(f"⏱ Нагрузка: {job.get('workload')}")

    if job.get("duration"):
        lines.append(f"📆 Длительность: {job.get('duration')}")

    if job.get("experience_level"):
        lines.append(f"🎚 Уровень: {job.get('experience_level')}")

    if job.get("project_type"):
        lines.append(f"📂 Тип проекта: {job.get('project_type')}")

    if skills:
        lines.append("🧠 Навыки: " + ", ".join(str(x) for x in skills[:15]))

    preferred_parts = []
    for label, key in [
        ("JSS", "job_success_score"),
        ("локация", "location"),
        ("язык", "languages"),
        ("английский", "english_level"),
    ]:
        if preferred.get(key):
            preferred_parts.append(f"{label}: {preferred.get(key)}")

    if preferred_parts:
        lines.append("⭐ Предпочтения клиента: " + "; ".join(preferred_parts))

    lines.append("📊 Конкуренция: " + format_competition_brief(job.get("competition") or {}))
    lines.append("👤 Клиент: " + format_client_brief(job.get("client") or {}))
    lines.append("📜 История клиента:\n" + format_history_brief(job.get("client_history") or []))

    description = str(job.get("description") or "").strip()
    if description:
        if len(description) > 1200:
            description = description[:1200].rstrip() + "..."
        lines.append("📝 Описание:\n" + description)

    return "\n\n".join(lines).strip()

def format_private_message(job: dict[str, Any], review: dict[str, Any]) -> list[str]:
    score = review.get("score", "?")
    category = review.get("category", "")
    title = review.get("title_guess") or job.get("title") or "Вакансия"

    apply_decision = review.get("apply_decision", "")
    connects_risk = review.get("connects_risk", "")
    recommended_next_action = review.get("recommended_next_action", "")

    why_good = "\n".join(f"✅ {item}" for item in review.get("why_good", []))
    risks = "\n".join(f"⚠️ {item}" for item in review.get("risks", []))
    missing = "\n".join(f"➖ {item}" for item in review.get("missing_skills", []))

    summary = review.get("decision_summary", "")
    salary = review.get("salary_comment", "")

    header = (
        f"⭐ AI Match: {score}% ({category})\n"
        f"📌 {title}\n"
    )

    if job.get("source") == "upwork":
        if apply_decision:
            header += f"🎯 Решение по Upwork: {apply_decision}\n"
        if connects_risk:
            header += f"🪙 Риск потратить Connects зря: {connects_risk}\n"

    header += "\n"

    if summary:
        header += f"{summary}\n\n"

    details = ""

    if recommended_next_action:
        details += f"Что делать:\n➡️ {recommended_next_action}\n\n"

    if why_good:
        details += f"Почему подходит:\n{why_good}\n\n"

    if risks:
        details += f"Риски:\n{risks}\n\n"

    if missing:
        details += f"Чего не хватает:\n{missing}\n\n"

    if salary:
        details += f"По доходу:\n{salary}\n\n"

    if job.get("source") == "upwork":
        client_name = review.get("client_name", "")
        client_reputation = review.get("client_reputation_summary", "")
        client_history_summary = review.get("client_history_summary", "")
        price_suggestion = review.get("upwork_price_suggestion", "")

        if client_name:
            details += f"Имя клиента для письма: {client_name}\n\n"

        if price_suggestion:
            details += f"Рекомендованная ставка: {price_suggestion}\n\n"

        if client_reputation:
            details += f"Репутация клиента:\n{client_reputation}\n\n"

        if client_history_summary:
            details += f"История клиента:\n{client_history_summary}\n\n"

    messages = [
        (header + details).strip(),
    ]

    questions = job.get("questions") or []
    question_answers = review.get("question_answers") or []

    if job.get("source") == "upwork":
        if questions or question_answers:
            questions_block = ""

            if questions:
                questions_block += "Вопросы Upwork:\n" + "\n".join(
                    f"{index}. {question}" for index, question in enumerate(questions, start=1)
                )
            else:
                questions_block += "Вопросы Upwork: нет"

            if question_answers:
                answers_lines = []

                for index, item in enumerate(question_answers, start=1):
                    if isinstance(item, dict):
                        question = item.get("question", "")
                        answer = item.get("answer", "")
                        answers_lines.append(f"{index}. Q: {question}\nA: {answer}")
                    else:
                        answers_lines.append(f"{index}. {item}")

                questions_block += "\n\nОтветы для Upwork:\n" + "\n\n".join(answers_lines)

            messages.append(questions_block.strip())

        messages.append("Данные вакансии:\n\n" + format_upwork_summary(job))
    else:
        job_text = build_structured_job_text(job)
        messages.append("Текст вакансии:\n" + job_text)

    cover_ru = review.get("cover_letter_ru", "")
    cover_en = review.get("cover_letter_en", "")

    if cover_ru:
        messages.append("Сопроводительное RU:\n\n" + cover_ru)

    if cover_en:
        messages.append("Cover letter EN:\n\n" + cover_en)

    return messages

async def send_long(bot: Bot, chat_id: int, text: str) -> None:
    limit = 3900
    text = text.strip()

    if len(text) <= limit:
        await bot.send_message(chat_id=chat_id, text=text)
        return

    for start in range(0, len(text), limit):
        await bot.send_message(chat_id=chat_id, text=text[start:start + limit])
        await asyncio.sleep(0.5)


def move_file(path: Path, target_dir: Path) -> Path:
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}_{datetime.now().strftime('%H%M%S')}{path.suffix}"
    shutil.move(str(path), str(target))
    return target


async def process_one_file(path: Path, bot: Bot, client: OpenAI) -> None:
    processing_path = move_file(path, PROCESSING_DIR)

    try:
        job = json.loads(processing_path.read_text(encoding="utf-8"))

        system_prompt, user_template, candidate_profile = load_prompts()
        user_prompt = build_user_prompt(user_template, candidate_profile, job)

        review = review_job(client, system_prompt, user_prompt)

        job["ai_review"] = review
        job["ai_reviewed_at"] = datetime.now().isoformat(timespec="seconds")

        recommend = bool(review.get("recommend"))
        score = int(review.get("score") or 0)
        if job.get("source") == "upwork":
            min_score = int(os.getenv("UPWORK_AI_MIN_SCORE_TO_NOTIFY", "50"))
        else:
            min_score = int(os.getenv("AI_MIN_SCORE_TO_NOTIFY", "75"))

        if recommend and score >= min_score:
            approved_path = APPROVED_DIR / processing_path.name
            approved_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            processing_path.unlink(missing_ok=True)

            for message in format_private_message(job, review):
                await send_long(bot, settings.admin_user_id, message)
                await asyncio.sleep(0.7)

            logger.info("Approved and sent: %s score=%s", approved_path.name, score)
            return

        rejected_path = REJECTED_DIR / processing_path.name
        rejected_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        processing_path.unlink(missing_ok=True)

        logger.info("Rejected: %s score=%s recommend=%s", rejected_path.name, score, recommend)

    except Exception as exc:
        logger.exception("AI review failed for %s: %s", processing_path.name, exc)
        error_path = ERRORS_DIR / processing_path.name
        try:
            move_file(processing_path, ERRORS_DIR)
        except Exception:
            pass

        await send_long(
            bot,
            settings.admin_user_id,
            f"❌ Ошибка AI-review для файла {path.name}:\n{exc}\n\nФайл перенесён в errors.",
        )


async def main() -> None:
    ensure_dirs()

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty")

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is empty")

    bot = Bot(token=settings.bot_token)
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    interval = int(os.getenv("AI_REVIEW_INTERVAL_SECONDS", "60"))

    logger.info("AI reviewer started. Inbox: %s", INBOX_DIR)

    try:
        while True:
            files = sorted(INBOX_DIR.glob("*.json"))

            if not files:
                await asyncio.sleep(interval)
                continue

            for path in files:
                await process_one_file(path, bot, client)
                await asyncio.sleep(1)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())