from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote_plus, urljoin

from seleniumbase import SB

from app.cleaner import clean_text, should_ignore
from app.config import BASE_DIR, settings
from app.logging_setup import setup_logging
from app.scoring import score_job
from app.storage import save_to_queue

setup_logging()
logger = logging.getLogger(__name__)

SEEN_FILE = BASE_DIR / "data" / "upwork_seen.txt"
UPWORK_BASE = "https://www.upwork.com"

MAX_UPWORK_PROPOSALS = int(os.getenv("MAX_UPWORK_PROPOSALS", "25"))
MAX_UPWORK_JOB_AGE_DAYS = int(os.getenv("MAX_UPWORK_JOB_AGE_DAYS", "2"))


def get_queries() -> list[str]:
    raw = os.getenv(
        "UPWORK_SEARCH_QUERIES",
        "python;"
        "python automation;"
        "python scraping;"
        "selenium python;"
        "playwright python;"
        "fastapi python;"
        "api integration python;"
        "google sheets python;"
        "telegram bot python;"
        "openai python;"
        "langgraph python;"
        "azure python;"
        "microsoft sentinel;"
        "security automation python",
    )
    return [q.strip() for q in raw.split(";") if q.strip()]


def build_url(query: str) -> str:
    return (
        "https://www.upwork.com/nx/search/jobs/"
        f"?amount=200-"
        f"&client_hires=1-9,10-"
        f"&hourly_rate=20-"
        f"&payment_verified=1"
        f"&proposals=0-4,5-9,10-14,15-19"
        f"&q={quote_plus(query)}"
        f"&sort=relevance%2Bdesc"
        f"&t=0,1"
    )


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    return {
        line.strip()
        for line in SEEN_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text("\n".join(sorted(seen)[-5000:]), encoding="utf-8")


def extract_upwork_job_uid(url: str, fallback: str = "") -> str:
    match = re.search(r"~(\d+)", url or "")
    if match:
        return match.group(1)
    return fallback or url or ""


def parse_posted_age_days(posted_text: str) -> int | None:
    lower = (posted_text or "").lower().strip()

    if not lower:
        return None

    if "just now" in lower:
        return 0

    if "minute" in lower or "hour" in lower:
        return 0

    if "yesterday" in lower:
        return 1

    match = re.search(r"posted\s+(\d+)\s+day", lower)
    if match:
        return int(match.group(1))

    match = re.search(r"posted\s+(\d+)\s+week", lower)
    if match:
        return int(match.group(1)) * 7

    match = re.search(r"posted\s+(\d+)\s+month", lower)
    if match:
        return int(match.group(1)) * 30

    match = re.search(r"posted\s+(\d+)\s+quarter", lower)
    if match:
        return int(match.group(1)) * 90

    if "last week" in lower:
        return 7

    if "last month" in lower:
        return 30

    if "last quarter" in lower:
        return 90

    return None


def is_fresh_job(posted_text: str, max_days: int = MAX_UPWORK_JOB_AGE_DAYS) -> bool:
    days = parse_posted_age_days(posted_text)

    # Для Upwork лучше не сохранять вакансию, если возраст не распознан.
    if days is None:
        logger.info("Skip Upwork job with unknown age: %s", posted_text)
        return False

    return days <= max_days


def parse_money_to_float(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.]", "", value or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int_from_text(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    return int(match.group(0))


def parse_proposals_min(value: str) -> int | None:
    value = (value or "").strip().lower()

    if not value:
        return None

    if "less than 5" in value:
        return 0

    if "+" in value:
        return parse_int_from_text(value)

    match = re.search(r"(\d+)\s*[-–—]\s*(\d+)", value)
    if match:
        return int(match.group(1))

    return parse_int_from_text(value)


def parse_bid_range(value: str) -> dict[str, float | str | None]:
    result: dict[str, float | str | None] = {
        "raw": value or "",
        "high": None,
        "avg": None,
        "low": None,
    }

    if not value:
        return result

    for key in ["High", "Avg", "Low"]:
        match = re.search(
            rf"{key}\s*\$\s*([\d,]+(?:\.\d+)?)",
            value,
            flags=re.IGNORECASE,
        )
        if match:
            result[key.lower()] = parse_money_to_float(match.group(1))

    return result


def parse_client_history_amount(value: str) -> float | None:
    if not value:
        return None

    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", value)
    if not match:
        return None

    return parse_money_to_float(match.group(1))


def should_skip_by_competition(competition: dict[str, Any]) -> tuple[bool, str]:
    proposals_min = competition.get("proposals_count_min")

    if isinstance(proposals_min, int) and proposals_min > MAX_UPWORK_PROPOSALS:
        return True, f"too many proposals: {competition.get('proposals')}"

    hires = competition.get("hires")
    if isinstance(hires, int) and hires > 0:
        return True, f"already hired on this job: {hires}"

    return False, ""


def has_hard_language_or_location_mismatch(details: dict[str, Any]) -> bool:
    description = str(details.get("description") or "").lower()
    preferred = details.get("preferred_qualifications") or {}
    preferred_text = " ".join(str(v) for v in preferred.values()).lower()

    bad_description_phrases = [
        "native portuguese speaker",
        "native portuguese",
        "communication in portuguese",
        "all business context is in brazilian portuguese",
        "portuguese speaker",
    ]

    if any(phrase in description for phrase in bad_description_phrases):
        return True

    if "portuguese" in preferred_text:
        return True

    preferred_location = str(preferred.get("location") or "").strip().lower()
    if preferred_location and preferred_location not in {
        "worldwide",
        "remote",
        "anywhere",
        "armenia",
    }:
        return True

    return False


def bad_by_text(text: str) -> bool:
    lower = text.lower()

    bad = [
        "payment unverified",
        "payment method not verified",
        "no hires",
        "less than $100",
        "wordpress",
        "shopify",
        "webflow",
    ]

    return any(x in lower for x in bad)


def split_upwork_questions(raw_questions: list[str]) -> list[str]:
    result: list[str] = []

    for item in raw_questions:
        text = item.strip()
        if not text:
            continue

        quoted = re.findall(r'"([^"]+)"', text)
        if quoted:
            result.extend(q.strip() for q in quoted if q.strip())
            continue

        lines = [line.strip().strip('"') for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            result.extend(lines)
            continue

        parts = re.split(r"\?\s+(?=[A-ZА-ЯЁ\"'])", text)
        for index, part in enumerate(parts):
            part = part.strip().strip('"')
            if not part:
                continue
            if index < len(parts) - 1 and not part.endswith("?"):
                part += "?"
            result.append(part)

    cleaned: list[str] = []
    seen: set[str] = set()

    for question in result:
        value = re.sub(r"\s+", " ", question).strip().strip('"')
        key = value.lower()

        if value and key not in seen:
            cleaned.append(value)
            seen.add(key)

    return cleaned


def extract_jobs_from_search_page(sb: SB) -> list[dict[str, str]]:
    script = r"""
    (() => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

        const cards = Array.from(document.querySelectorAll('article[data-test="JobTile"]'));

        return cards.map(card => {
            const link = card.querySelector('a[href*="/jobs/"]');
            const href = link ? link.href : '';
            const title = link ? clean(link.innerText) : '';
            const cardText = card.innerText.trim();
            const lines = cardText.split('\n').map(x => x.trim()).filter(Boolean);
            const postedText = lines.find(x => /^Posted\b/i.test(x)) || lines[0] || '';
            const uid =
                card.getAttribute('data-test-key')
                || card.getAttribute('data-ev-job-uid')
                || '';

            return {title, href, cardText, postedText, uid};
        });
    })()
    """

    jobs = sb.execute_script(script) or []
    result: list[dict[str, str]] = []

    for job in jobs:
        title = (job.get("title") or "").strip()
        href = (job.get("href") or "").strip()
        card_text = (job.get("cardText") or "").strip()
        uid = (job.get("uid") or "").strip()
        posted_text = (job.get("postedText") or "").strip()

        if not is_fresh_job(posted_text):
            logger.info("Skip old Upwork job: %s | %s", posted_text, title[:80])
            continue

        url = urljoin(UPWORK_BASE, href) if href else ""

        if not title or title.lower().startswith("posted "):
            lines = [line.strip() for line in card_text.splitlines() if line.strip()]
            title = next(
                (line for line in lines if not line.lower().startswith("posted ")),
                "Upwork job",
            )[:160]

        if len(card_text) < 80:
            continue

        result.append(
            {
                "title": title,
                "url": url,
                "card_text": card_text,
                "uid": uid,
                "posted_text": posted_text,
            }
        )

    return result


def get_upwork_job_details(sb: SB, url: str) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "description": "",
        "questions": [],
        "questions_count": 0,
        "has_questions": False,
        "skills": [],
        "budget": {},
        "competition": {},
        "client": {},
        "client_history": [],
        "client_name_guess": "",
        "preferred_qualifications": {},
        "workload": "",
        "duration": "",
        "experience_level": "",
        "contract_to_hire": False,
        "project_type": "",
        "other_open_jobs": [],
        "needs_to_hire": "",
    }

    if not url:
        return empty

    try:
        logger.info("Opening job page: %s", url)
        sb.open(url)
        sb.wait_for_element(
            ".job-details-content, [data-test='Description'], p.multiline-text",
            timeout=20,
        )

        try:
            sb.wait_for_element('[data-test="about-client-container"]', timeout=10)
        except Exception:
            logger.info("About client block did not appear in time: %s", url)

        time.sleep(1)

        script = r"""
        (() => {
            const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const text = (el) => clean(el ? el.innerText : '');
            const allText = document.body ? document.body.innerText : '';

            const descriptionEl =
                document.querySelector('[data-test="Description"] p.multiline-text')
                || document.querySelector('p.text-body-sm.multiline-text')
                || document.querySelector('.multiline-text');

            let questions = [];
            const questionHeader = Array.from(document.querySelectorAll('strong'))
                .find(el => /You will be asked to answer the following questions/i.test(el.innerText || ''));

            if (questionHeader) {
                let root = questionHeader.closest('section') || questionHeader.parentElement;
                let list = root ? root.querySelector('ol, ul') : null;

                if (!list && questionHeader.parentElement) {
                    let next = questionHeader.parentElement.nextElementSibling;
                    while (next && !list) {
                        if (next.matches && next.matches('ol, ul')) {
                            list = next;
                            break;
                        }
                        list = next.querySelector ? next.querySelector('ol, ul') : null;
                        next = next.nextElementSibling;
                    }
                }

                if (list) {
                    questions = Array.from(list.querySelectorAll('li'))
                        .map(li => clean(li.innerText))
                        .filter(Boolean);
                }
            }

            const skillsSection = Array.from(document.querySelectorAll('section'))
                .find(section => /Skills and Expertise/i.test(section.innerText || ''));

            const skills = skillsSection
                ? Array.from(skillsSection.querySelectorAll('a.air3-badge, .air3-badge, [id^="air3-line-clamp"]'))
                    .map(el => clean(el.innerText))
                    .filter(Boolean)
                    .filter((v, i, arr) => arr.indexOf(v) === i)
                    .filter(v => !/^\+\s*\d+\s*more$/i.test(v))
                : [];

            const activitySection = Array.from(document.querySelectorAll('section'))
                .find(section => /Activity on this job/i.test(section.innerText || ''));

            const competition = {};
            if (activitySection) {
                const rows = Array.from(activitySection.querySelectorAll('li.ca-item'));
                for (const row of rows) {
                    const label = clean((row.querySelector('.title') || {}).innerText || '').replace(':', '');
                    const value = clean((row.querySelector('.value') || {}).innerText || '');

                    if (/Proposals/i.test(label)) competition.proposals = value;
                    if (/Last viewed/i.test(label)) competition.last_viewed_by_client = value;
                    if (/Interviewing/i.test(label)) competition.interviewing = value;
                    if (/Invites sent/i.test(label)) competition.invites_sent = value;
                    if (/Unanswered invites/i.test(label)) competition.unanswered_invites = value;
                    if (/Hires/i.test(label)) competition.hires = value;
                }
            }

            const bidEl = Array.from(document.querySelectorAll('h5, strong'))
                .find(el => /^Bid range/i.test(clean(el.innerText || '')));

            competition.bid_range_text = text(bidEl);

            const preferred = {};

            for (const strong of Array.from(document.querySelectorAll('strong'))) {
                const label = clean(strong.innerText || '').replace(':', '');
                const parent = strong.parentElement;
                const span = parent ? parent.querySelector('span:not(.icons)') : null;
                const value = text(span);

                if (/Job Success Score/i.test(label)) preferred.job_success_score = value;
                if (/Location/i.test(label)) preferred.location = value;
                if (/Languages/i.test(label)) preferred.languages = value;
                if (/English level/i.test(label)) preferred.english_level = value;
            }

            const about = document.querySelector('[data-test="about-client-container"], .cfe-ui-job-about-client');
            const client = {};

            if (about) {
                const aboutText = about.innerText || '';

                client.payment_verified = /Payment method verified/i.test(aboutText);
                client.payment_not_verified = /Payment method not verified/i.test(aboutText);
                client.phone_verified = /Phone number verified/i.test(aboutText);

                const ratingMatch = aboutText.match(/(\d+(?:\.\d+)?)\s+of\s+(\d+)\s+reviews/i);
                if (ratingMatch) {
                    client.rating = ratingMatch[1];
                    client.reviews_count = ratingMatch[2];
                }

                client.country = text(about.querySelector('[data-qa="client-location"] strong'));
                client.location_text = text(about.querySelector('[data-qa="client-location"] div'));

                client.jobs_posted_text = text(about.querySelector('[data-qa="client-job-posting-stats"] strong'));
                client.hire_rate_text = text(about.querySelector('[data-qa="client-job-posting-stats"] div'));

                client.total_spent = text(about.querySelector('[data-qa="client-spend"]'));
                client.hires_text = text(about.querySelector('[data-qa="client-hires"]'));

                client.avg_hourly_rate_paid = text(about.querySelector('[data-qa="client-hourly-rate"]'));
                client.hours = text(about.querySelector('[data-qa="client-hours"]'));

                client.company_industry = text(about.querySelector('[data-qa="client-company-profile-industry"]'));
                client.company_size = text(about.querySelector('[data-qa="client-company-profile-size"]'));

                client.member_since = text(about.querySelector('[data-qa="client-contract-date"] small'));

                if (!client.member_since) {
                    const memberMatch = aboutText.match(/Member since\s+([^\n]+)/i);
                    if (memberMatch) client.member_since = clean(memberMatch[1]);
                }
            }
            const otherOpenJobsRoot = document.querySelector('#otherOpenJobs');
            let otherOpenJobs = [];

            if (otherOpenJobsRoot) {
                otherOpenJobs = Array.from(otherOpenJobsRoot.querySelectorAll('li'))
                    .map(li => {
                        const a = li.querySelector('a');
                        const typeEl = li.querySelector('.type');
                        return {
                            title: text(a),
                            url: a ? a.href : '',
                            type: text(typeEl),
                        };
                    })
                    .filter(item => item.title);
            }
            const features = Array.from(document.querySelectorAll('ul.features li')).map(li => ({
                strong: clean((li.querySelector('strong') || {}).innerText || ''),
                description: clean((li.querySelector('.description') || {}).innerText || ''),
                text: clean(li.innerText || ''),
            }));

            const contractToHire = /Contract-to-hire opportunity/i.test(allText);

            const projectTypeMatch = allText.match(/Project Type:\s*([^\n]+)/i);
            const needsToHireMatch = allText.match(/Needs to hire\s+([^\n]+)/i);
            let history = [];

            const historyRoot = document.querySelector('section[data-cy="jobs"]');

            if (historyRoot) {
                history = Array.from(historyRoot.querySelectorAll('.item[data-cy="job"]'))
                    .map(item => {
                        const titleEl = item.querySelector('[data-cy="job-title"]');
                        const freelancerLink = Array.from(item.querySelectorAll('a.up-n-link'))
                            .find(a => /\/freelancers\//i.test(a.getAttribute('href') || ''));

                        const dateEl = item.querySelector('[data-cy="date"]');
                        const statsEl = item.querySelector('[data-cy="stats"]');

                        const ratingTexts = Array.from(item.querySelectorAll('.air3-rating-value-text'))
                            .map(el => clean(el.innerText))
                            .filter(Boolean);

                        const truncationTexts = Array.from(item.querySelectorAll('.air3-truncation span[id^="air3-truncation-"]'))
                            .map(el => clean(el.innerText))
                            .filter(Boolean);

                        const itemText = item.innerText || '';

                        return {
                            raw: itemText.trim(),
                            title: text(titleEl),
                            freelancer_name: text(freelancerLink),
                            period: text(dateEl),
                            stats: text(statsEl),
                            client_rating: ratingTexts[0] || '',
                            freelancer_rating: ratingTexts[1] || '',
                            client_review: truncationTexts[0] || '',
                            freelancer_review: truncationTexts[1] || '',
                        };
                    })
                    .filter(item => item.title);
            }

            const titleEl =
                document.querySelector('.job-details-content h4 span.flex-1')
                || document.querySelector('h4 span.flex-1')
                || document.querySelector('h1')
                || document.querySelector('h4');

            const postedEl = Array.from(document.querySelectorAll('.posted-on-line div, .posted-on-line, section div'))
                .find(el => /^Posted\b/i.test(clean(el.innerText || '')));
            const bodyText = document.body ? document.body.innerText : '';

            function sliceBetween(text, start, endMarkers) {
                const startIndex = text.indexOf(start);
                if (startIndex === -1) return '';

                let part = text.slice(startIndex + start.length);

                for (const marker of endMarkers) {
                    const idx = part.indexOf(marker);
                    if (idx !== -1) {
                        part = part.slice(0, idx);
                    }
                }

                return part.trim();
            }

            const questionsTextFallback = sliceBetween(
                bodyText,
                'You will be asked to answer the following questions when submitting a proposal:',
                ['Skills and Expertise', 'Activity on this job', 'About the client']
            );

            const preferredTextFallback = sliceBetween(
                bodyText,
                'Preferred qualifications',
                ['Activity on this job', 'Send a proposal', 'About the client']
            );

            const clientTextFallback = sliceBetween(
                bodyText,
                'About the client',
                ['Job link', "Client's recent history", 'Other open jobs by this Client', 'Footer navigation']
            );

            const clientHistoryTextFallback = sliceBetween(
                bodyText,
                "Client's recent history",
                ['Other open jobs by this Client', 'Footer navigation']
            );

            const otherOpenJobsTextFallback = sliceBetween(
                bodyText,
                'Other open jobs by this Client',
                ['Footer navigation']
            );
            return {
                page_title: text(titleEl),
                posted_at_text: text(postedEl),
                description: text(descriptionEl),
                questions,
                skills,
                competition,
                preferred_qualifications: preferred,
                client,
                features,
                contract_to_hire: contractToHire,
                project_type: projectTypeMatch ? clean(projectTypeMatch[1]) : '',
                needs_to_hire: needsToHireMatch ? clean(needsToHireMatch[1]) : '',
                other_open_jobs: otherOpenJobs,
                client_history_raw: history,
                questions_text_fallback: questionsTextFallback,
                preferred_text_fallback: preferredTextFallback,
                client_text_fallback: clientTextFallback,
                client_history_text_fallback: clientHistoryTextFallback,
                other_open_jobs_text_fallback: otherOpenJobsTextFallback,
            };
        })()
        """

        raw = sb.execute_script(script) or {}
        details = normalize_upwork_details(raw)
        return {**empty, **details}

    except Exception as exc:
        logger.warning("Could not get full Upwork details %s: %s", url, exc)
        return empty


def normalize_upwork_details(raw: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}

    description = str(raw.get("description") or "").strip()

    raw_questions = [
        str(q).strip()
        for q in raw.get("questions") or []
        if str(q).strip()
    ]
    questions = split_upwork_questions(raw_questions)
    if not questions:
        fallback = str(raw.get("questions_text_fallback") or "").strip()
        if fallback:
            quoted = re.findall(r'"([^"]+)"', fallback)
            if quoted:
                questions = [q.strip() for q in quoted if q.strip()]
            else:
                questions = [
                    line.strip().strip('"')
                    for line in fallback.splitlines()
                    if line.strip()
                ]
    skills = [
        str(s).strip()
        for s in raw.get("skills") or []
        if str(s).strip()
    ]

    details["page_title"] = str(raw.get("page_title") or "").strip()
    details["posted_at_text"] = str(raw.get("posted_at_text") or "").strip()
    details["description"] = description
    details["questions"] = questions
    details["questions_count"] = len(questions)
    details["has_questions"] = bool(questions)
    details["skills"] = skills

    competition_raw = raw.get("competition") or {}

    proposals = str(competition_raw.get("proposals") or "").strip()
    bid_range_text = str(competition_raw.get("bid_range_text") or "").strip()
    bid_range = parse_bid_range(bid_range_text)

    competition = {
        "proposals": proposals,
        "proposals_count_min": parse_proposals_min(proposals),
        "last_viewed_by_client": str(competition_raw.get("last_viewed_by_client") or "").strip(),
        "interviewing": parse_int_from_text(str(competition_raw.get("interviewing") or "")),
        "invites_sent": parse_int_from_text(str(competition_raw.get("invites_sent") or "")),
        "unanswered_invites": parse_int_from_text(str(competition_raw.get("unanswered_invites") or "")),
        "hires": parse_int_from_text(str(competition_raw.get("hires") or "")),
        "bid_range_text": bid_range_text,
        "bid_high": bid_range.get("high"),
        "bid_avg": bid_range.get("avg"),
        "bid_low": bid_range.get("low"),
    }
    details["competition"] = competition

    preferred_raw = raw.get("preferred_qualifications") or {}
    preferred = {
        "job_success_score": str(preferred_raw.get("job_success_score") or "").strip(),
        "location": str(preferred_raw.get("location") or "").strip(),
        "languages": str(preferred_raw.get("languages") or "").strip(),
        "english_level": str(preferred_raw.get("english_level") or "").strip(),
    }

    preferred_fallback = str(raw.get("preferred_text_fallback") or "")
    if preferred_fallback:
        if not preferred["job_success_score"]:
            match = re.search(r"Job Success Score:\s*([^\n]+)", preferred_fallback, flags=re.I)
            if match:
                preferred["job_success_score"] = match.group(1).strip()

        if not preferred["location"]:
            match = re.search(r"Location:\s*([^\n]+)", preferred_fallback, flags=re.I)
            if match:
                preferred["location"] = match.group(1).strip()

        if not preferred["languages"]:
            match = re.search(r"Languages:\s*([^\n]+)", preferred_fallback, flags=re.I)
            if match:
                preferred["languages"] = match.group(1).strip()

        if not preferred["english_level"]:
            match = re.search(r"English level:\s*([^\n]+)", preferred_fallback, flags=re.I)
            if match:
                preferred["english_level"] = match.group(1).strip()
    details["preferred_qualifications"] = preferred

    client_raw = raw.get("client") or {}

    hire_rate_text = str(client_raw.get("hire_rate_text") or "")
    hires_text = str(client_raw.get("hires_text") or "")
    jobs_posted_text = str(client_raw.get("jobs_posted_text") or "")
    location_text = str(client_raw.get("location_text") or "")

    location_parts = location_text.split()
    city = " ".join(location_parts[:-2]) if len(location_parts) > 2 else location_text

    client = {
        "payment_verified": bool(client_raw.get("payment_verified")),
        "payment_not_verified": bool(client_raw.get("payment_not_verified")),
        "phone_verified": bool(client_raw.get("phone_verified")),
        "rating": parse_money_to_float(str(client_raw.get("rating") or "")),
        "reviews_count": parse_int_from_text(str(client_raw.get("reviews_count") or "")),
        "country": str(client_raw.get("country") or "").strip(),
        "location_text": location_text,
        "city": city,
        "jobs_posted": parse_int_from_text(jobs_posted_text),
        "hire_rate": parse_int_from_text(hire_rate_text),
        "open_jobs": parse_int_from_text(hire_rate_text.split(",")[-1] if "," in hire_rate_text else ""),
        "total_spent": str(client_raw.get("total_spent") or "").strip(),
        "hires": parse_int_from_text(hires_text.split(",")[0] if hires_text else ""),
        "active_hires": parse_int_from_text(hires_text.split(",")[-1] if "," in hires_text else ""),
        "avg_hourly_rate_paid": str(client_raw.get("avg_hourly_rate_paid") or "").strip(),
        "hours": str(client_raw.get("hours") or "").strip(),
        "company_industry": str(client_raw.get("company_industry") or "").strip(),
        "company_size": str(client_raw.get("company_size") or "").strip(),
        "member_since": str(client_raw.get("member_since") or "").strip(),
    }
    client_fallback = str(raw.get("client_text_fallback") or "")
    if client_fallback:
        if "Payment method verified" in client_fallback:
            client["payment_verified"] = True
        if "Payment method not verified" in client_fallback:
            client["payment_not_verified"] = True
        if "Phone number verified" in client_fallback:
            client["phone_verified"] = True

        if not client["rating"]:
            match = re.search(r"(\d+(?:\.\d+)?)\s+of\s+(\d+)\s+reviews", client_fallback, flags=re.I)
            if match:
                client["rating"] = parse_money_to_float(match.group(1))
                client["reviews_count"] = parse_int_from_text(match.group(2))

        if not client["country"]:
            lines = [x.strip() for x in client_fallback.splitlines() if x.strip()]
            for i, line in enumerate(lines):
                if re.fullmatch(r"[A-Z][A-Za-z ]+", line) and i + 1 < len(lines):
                    if re.search(r"\d{1,2}:\d{2}", lines[i + 1]):
                        client["country"] = line
                        client["location_text"] = lines[i + 1]
                        break

        if not client["jobs_posted"]:
            match = re.search(r"(\d+)\s+jobs posted", client_fallback, flags=re.I)
            if match:
                client["jobs_posted"] = int(match.group(1))

        if not client["hire_rate"]:
            match = re.search(r"(\d+)%\s+hire rate", client_fallback, flags=re.I)
            if match:
                client["hire_rate"] = int(match.group(1))

        if not client["open_jobs"]:
            match = re.search(r"(\d+)\s+open jobs", client_fallback, flags=re.I)
            if match:
                client["open_jobs"] = int(match.group(1))

        if not client["total_spent"]:
            match = re.search(r"\$[\d,.]+\s*[KkMm]?\s+total spent", client_fallback)
            if match:
                client["total_spent"] = match.group(0)

        if not client["hires"]:
            match = re.search(r"(\d+)\s+hire", client_fallback, flags=re.I)
            if match:
                client["hires"] = int(match.group(1))

        if not client["active_hires"]:
            match = re.search(r"(\d+)\s+active", client_fallback, flags=re.I)
            if match:
                client["active_hires"] = int(match.group(1))

        if not client["avg_hourly_rate_paid"]:
            match = re.search(r"\$[\d,.]+\s*/hr\s+avg hourly rate paid", client_fallback, flags=re.I)
            if match:
                client["avg_hourly_rate_paid"] = match.group(0)

        if not client["hours"]:
            match = re.search(r"\d+\s+hours?", client_fallback, flags=re.I)
            if match:
                client["hours"] = match.group(0)

        if not client["member_since"]:
            match = re.search(r"Member since\s+([^\n]+)", client_fallback, flags=re.I)
            if match:
                client["member_since"] = match.group(1).strip()
    details["client"] = client
    other_open_jobs = raw.get("other_open_jobs") or []
    details["other_open_jobs"] = [
        {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "type": str(item.get("type") or "").strip(),
        }
        for item in other_open_jobs
        if str(item.get("title") or "").strip()
    ]
    features = raw.get("features") or []

    budget_min = None
    budget_max = None
    workload = ""
    duration = ""
    experience_level = ""
    budget_type = ""

    for item in features:
        strong = str(item.get("strong") or "").strip()
        desc = str(item.get("description") or "").strip()
        item_text = str(item.get("text") or "").strip()
        item_lower = item_text.lower()
        desc_lower = desc.lower()
        strong_lower = strong.lower()

        amounts = re.findall(r"\$\s*[\d,]+(?:\.\d+)?", item_text)

        # Зарплата/бюджет — только feature-блок, где description = Hourly или Fixed-price
        # и где реально есть $.
        if amounts and desc_lower in {"hourly", "fixed-price"}:
            budget_min = parse_money_to_float(amounts[0])
            budget_max = parse_money_to_float(amounts[1]) if len(amounts) > 1 else None
            budget_type = desc
            continue

        if "hrs/week" in strong_lower:
            workload = strong
            continue

        if "month" in strong_lower:
            duration = strong
            continue

        if strong_lower in {"entry", "intermediate", "expert"}:
            experience_level = strong
            continue

    details["budget"] = {
        "type": budget_type,
        "min": budget_min,
        "max": budget_max,
    }
    details["workload"] = workload
    details["duration"] = duration
    details["experience_level"] = experience_level
    details["contract_to_hire"] = bool(raw.get("contract_to_hire"))
    details["project_type"] = str(raw.get("project_type") or "").strip()
    details["needs_to_hire"] = str(raw.get("needs_to_hire") or "").strip()
    history_raw = [
        str(x).strip()
        for x in raw.get("client_history_raw") or []
        if str(x).strip()
    ]

    details["client_history"] = parse_client_history(history_raw)
    details["client_name_guess"] = guess_client_name_from_history(details["client_history"])

    return details


def parse_client_history(history_raw: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in history_raw:
        if isinstance(item, dict):
            raw = str(item.get("raw") or "").strip()
            title = str(item.get("title") or "").strip()
            freelancer_name = str(item.get("freelancer_name") or "").strip()
            period = str(item.get("period") or "").strip()
            stats = str(item.get("stats") or "").strip()
            client_rating = parse_money_to_float(str(item.get("client_rating") or ""))
            freelancer_rating = parse_money_to_float(str(item.get("freelancer_rating") or ""))
            client_review = str(item.get("client_review") or "").strip()
            freelancer_review = str(item.get("freelancer_review") or "").strip()
        else:
            raw = str(item or "").strip()
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            title = lines[0] if lines else ""
            freelancer_name = ""
            period = ""
            stats = ""
            client_rating = None
            freelancer_rating = None
            client_review = ""
            freelancer_review = ""

        if not raw and not title:
            continue

        key = f"{title}|{freelancer_name}|{period}|{stats}"
        if key in seen:
            continue
        seen.add(key)

        billed_match = re.search(r"Billed:\s*\$[\d,]+(?:\.\d+)?", stats or raw, flags=re.I)
        hourly_match = re.search(r"\d+\s+hrs?\s*@\s*\$[\d,]+(?:\.\d+)?/hr", stats or raw, flags=re.I)
        fixed_match = re.search(r"Fixed-price\s*\$[\d,]+(?:\.\d+)?", stats or raw, flags=re.I)

        amount_text = ""
        amount = None

        if billed_match:
            amount_text = billed_match.group(0)
            amount = parse_client_history_amount(amount_text)
        elif fixed_match:
            amount_text = fixed_match.group(0)
            amount = parse_client_history_amount(amount_text)
        elif hourly_match:
            amount_text = hourly_match.group(0)
            amount = parse_client_history_amount(amount_text)

        hourly_rate = None
        hours = None

        rate_match = re.search(r"@\s*\$([\d,]+(?:\.\d+)?)/hr", stats or raw, flags=re.I)
        if rate_match:
            hourly_rate = parse_money_to_float(rate_match.group(1))

        hours_match = re.search(r"(\d+(?:\.\d+)?)\s+hrs?", stats or raw, flags=re.I)
        if hours_match:
            hours = parse_money_to_float(hours_match.group(1))

        result.append(
            {
                "raw": raw,
                "title": title,
                "freelancer_name": freelancer_name,
                "period": period,
                "stats": stats,
                "amount_text": amount_text,
                "amount": amount,
                "hours": hours,
                "hourly_rate": hourly_rate,
                "client_rating": client_rating,
                "freelancer_rating": freelancer_rating,
                "client_review": client_review,
                "freelancer_review": freelancer_review,
            }
        )

    return result[:5]


def guess_client_name_from_history(history: list[dict[str, Any]]) -> str:
    for item in history:
        raw = str(item.get("raw") or "")

        match = re.search(r"\b([A-Z][a-z]{2,})\s+(?:was|is)\s+", raw)
        if match:
            name = match.group(1)
            if name.lower() not in {
                "great",
                "clear",
                "fixed",
                "hourly",
                "rating",
                "billed",
                "freelancer",
            }:
                return name

    return ""


def build_ai_text(
    *,
    title: str,
    posted_text: str,
    url: str,
    details: dict[str, Any],
) -> str:
    parts: list[str] = []

    if title:
        parts.append(f"Title: {title}")

    if posted_text:
        parts.append(f"Posted: {posted_text}")

    if details.get("needs_to_hire"):
        parts.append("Needs to hire: " + str(details["needs_to_hire"]))

    if details.get("description"):
        parts.append("Description:\n" + str(details["description"]))

    if details.get("skills"):
        parts.append("Skills: " + ", ".join(details["skills"]))

    if details.get("questions"):
        questions_text = "\n".join(
            f"{i}. {q}"
            for i, q in enumerate(details["questions"], start=1)
        )
        parts.append("Proposal questions:\n" + questions_text)
    else:
        parts.append("Proposal questions: none")

    if details.get("preferred_qualifications"):
        parts.append("Preferred qualifications: " + str(details["preferred_qualifications"]))

    if details.get("budget"):
        parts.append("Budget: " + str(details["budget"]))

    if details.get("competition"):
        parts.append("Competition: " + str(details["competition"]))

    if details.get("client"):
        client = dict(details["client"])

        # Не даём scoring.py принять client total_spent за зарплату.
        # Эти данные остаются в JSON, но не попадают в текст для score/header.
        for key in [
            "total_spent",
            "avg_hourly_rate_paid",
            "hours",
        ]:
            client.pop(key, None)

        parts.append("Client: " + str(client))

    if details.get("client_history"):
        parts.append("Client recent history: " + str(details["client_history"]))

    if details.get("needs_to_hire"):
        parts.append("Needs to hire: " + str(details["needs_to_hire"]))

    if details.get("client_name_guess"):
        parts.append("Possible client name from reviews: " + str(details["client_name_guess"]))

    if url:
        parts.append("URL: " + url)

    return "\n\n".join(parts).strip()


def parse_search_results(sb: SB, query: str, seen: set[str]) -> int:
    search_url = build_url(query)
    logger.info("Opening Upwork search: %s", search_url)

    sb.open(search_url)
    page = sb.get_page_source().lower()
    if "verify you are human" in sb.get_text("body").lower():
        raise RuntimeError("Upwork requested CAPTCHA")
    logger.info("Current URL: %s", sb.get_current_url())
    logger.info("Page title: %s", sb.get_title())

    sb.wait_for_element('article[data-test="JobTile"]', timeout=60)
    time.sleep(2)

    jobs = extract_jobs_from_search_page(sb)
    logger.info("Upwork fresh jobs found for '%s': %s", query, len(jobs))

    added = 0

    for job in jobs[:10]:
        title = job["title"]
        url = job["url"]
        card_text = job["card_text"]
        uid = job["uid"]
        posted_text = job.get("posted_text", "")

        job_id = extract_upwork_job_uid(url, uid) or f"{query}:{hash(card_text)}"

        if job_id in seen:
            logger.info("Skip already seen Upwork job: %s | %s", posted_text, title[:80])
            continue

        details = get_upwork_job_details(sb, url)

        if details.get("page_title") and not str(details["page_title"]).lower().startswith("posted "):
            title = str(details["page_title"])

        if details.get("posted_at_text"):
            posted_text = str(details["posted_at_text"])

        if not is_fresh_job(posted_text):
            logger.info("Skip old Upwork job after details: %s | %s", posted_text, title[:80])
            seen.add(job_id)
            continue

        seen.add(job_id)

        description = str(details.get("description") or "").strip()

        if not description:
            logger.info("Skip Upwork job without full description/details: %s | %s", posted_text, title[:80])
            continue

        skip, reason = should_skip_by_competition(details.get("competition") or {})
        if skip:
            logger.info("Skip Upwork job by competition: %s | %s", reason, title[:80])
            continue

        text_for_filter = build_ai_text(
            title=title,
            posted_text=posted_text,
            url=url,
            details=details,
        )

        if not text_for_filter:
            text_for_filter = "\n\n".join(
                part
                for part in [posted_text, title, card_text]
                if part
            )

        if should_ignore(text_for_filter) or bad_by_text(text_for_filter):
            logger.info("Skip ignored/bad Upwork job: %s | %s", posted_text, title[:80])
            continue

        clean_for_score = clean_text(text_for_filter)
        result = score_job(clean_for_score)

        # Для Upwork зарплату берём только из структурного блока budget,
        # а не из общего текста, где есть client.total_spent.
        budget = details.get("budget") or {}
        budget_type = str(budget.get("type") or "").strip()
        budget_min = budget.get("min")
        budget_max = budget.get("max")

        if budget_min is not None and budget_max is not None:
            result.salary = f"${budget_min:g} - ${budget_max:g} {budget_type}".strip()
        elif budget_min is not None:
            result.salary = f"${budget_min:g} {budget_type}".strip()
        else:
            result.salary = None

        if result.score < settings.min_score_to_queue:
            logger.info("Skip weak Upwork job %s%%: %s", result.score, title[:80])
            continue

        final_text = f"{result.header()}\n\n{clean_for_score}"

        save_to_queue(
            text=final_text,
            score=result.score,
            source_channel="upwork",
            source="upwork",
            source_name=f"Upwork: {query}",
            url=url,
            title=title,
            posted_at_text=posted_text,
            description=description,
            questions=details.get("questions") or [],
            questions_count=int(details.get("questions_count") or 0),
            has_questions=bool(details.get("has_questions")),
            skills=details.get("skills") or [],
            budget=details.get("budget") or {},
            workload=str(details.get("workload") or ""),
            duration=str(details.get("duration") or ""),
            experience_level=str(details.get("experience_level") or ""),
            contract_to_hire=bool(details.get("contract_to_hire")),
            project_type=str(details.get("project_type") or ""),
            competition=details.get("competition") or {},
            client=details.get("client") or {},
            client_history=details.get("client_history") or [],
            client_name_guess=str(details.get("client_name_guess") or ""),
            preferred_qualifications=details.get("preferred_qualifications") or {},
            extra={
                "needs_to_hire": details.get("needs_to_hire") or "",
                "other_open_jobs": details.get("other_open_jobs") or [],
            },
        )

        logger.info("Queued Upwork job %s%%: %s | %s", result.score, posted_text, title[:80])
        added += 1

        time.sleep(1)

    return added


def run_once_sync() -> None:
    queries = get_queries()
    seen = load_seen()

    with SB(
        uc=True,
        headless2=True,
        user_data_dir=str(BASE_DIR / "data" / "chrome_upwork_profile"),
    ) as sb:
        total_added = 0

        for query in queries:
            try:
                total_added += parse_search_results(sb, query, seen)
            except Exception as exc:
                logger.exception("Failed Upwork query '%s': %s", query, exc)

            time.sleep(3)

    save_seen(seen)
    logger.info("Upwork parser finished. Added: %s", total_added)


async def main() -> None:
    interval = int(os.getenv("UPWORK_PARSER_INTERVAL_MINUTES", "30"))

    logger.info("Upwork SeleniumBase parser started")

    while True:
        await asyncio.to_thread(run_once_sync)
        await asyncio.sleep(interval * 60)


if __name__ == "__main__":
    asyncio.run(main())