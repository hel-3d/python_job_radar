from __future__ import annotations

import logging
import re
from telethon import TelegramClient, events
from .cleaner import clean_text, should_ignore
from .config import settings
from .logging_setup import setup_logging
from .scoring import score_job
from .storage import (
    add_unique_item,
    is_duplicate,
    load_json_list,
    load_post_cache,
    remove_item_by_number,
    save_post_cache,
    save_to_queue,
)

setup_logging()
logger = logging.getLogger(__name__)

cached_posts: list[str] = []
client = TelegramClient(settings.telegram_session_name, settings.telegram_api_id, settings.telegram_api_hash)


def load_source_channels() -> list[str]:
    dynamic_sources = load_json_list(settings.dynamic_sources_file)
    sources: list[str] = []
    seen: set[str] = set()
    for source in settings.source_channels + dynamic_sources:
        value = source.strip()
        key = value.lower()
        if value and key not in seen:
            sources.append(value)
            seen.add(key)
    return sources


def is_admin(event) -> bool:
    return bool(event.sender_id and int(event.sender_id) == settings.admin_user_id)


def is_admin_channel(chat_name: str | None, chat_id: int | None) -> bool:
    target = str(settings.target_channel_id).strip()
    if not target:
        return False
    if target.startswith("@"):
        return (chat_name or "").lower() == target[1:].lower()
    return str(chat_id) == target


def format_numbered(items: list[str]) -> str:
    if not items:
        return "пусто"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


async def handle_admin_command(event, text: str) -> bool:
    if not is_admin(event):
        return False

    command, _, arg = text.partition(" ")
    command = command.lower().strip()
    arg = arg.strip()

    if command not in {"/ban", "/unban", "/listban", "/cut", "/uncut", "/listcut", "/regexcut", "/unregexcut", "/listregexcut", "/addsource", "/removesource", "/listsources"}:
        return False

    try:
        if command == "/ban":
            if not arg:
                await event.reply("⚠️ Напиши так: /ban фраза")
                return True
            added, _ = add_unique_item(settings.banned_phrases_file, arg)
            await event.reply(("✅ Добавлено в ban: " if added else "⚠️ Уже есть в ban: ") + arg)
            return True

        if command == "/unban":
            removed, _ = remove_item_by_number(settings.banned_phrases_file, int(arg))
            await event.reply(f"✅ Удалено из ban: {removed}" if removed else "⚠️ Не нашла такой номер")
            return True

        if command == "/listban":
            await event.reply("🚫 Ban-фразы:\n" + format_numbered(load_json_list(settings.banned_phrases_file)))
            return True

        if command == "/cut":
            if not arg:
                await event.reply("⚠️ Напиши так: /cut фраза")
                return True
            added, _ = add_unique_item(settings.cut_phrases_file, arg)
            await event.reply(("✅ Добавлено в cut: " if added else "⚠️ Уже есть в cut: ") + arg)
            return True

        if command == "/uncut":
            removed, _ = remove_item_by_number(settings.cut_phrases_file, int(arg))
            await event.reply(f"✅ Удалено из cut: {removed}" if removed else "⚠️ Не нашла такой номер")
            return True

        if command == "/listcut":
            await event.reply("✂️ Cut-фразы:\n" + format_numbered(load_json_list(settings.cut_phrases_file)))
            return True

        if command == "/regexcut":
            if not arg:
                await event.reply("⚠️ Напиши так: /regexcut регулярка")
                return True
            try:
                re.compile(arg)
            except re.error as exc:
                await event.reply(f"⚠️ Ошибка в регулярке: {exc}")
                return True
            added, _ = add_unique_item(settings.regex_cut_phrases_file, arg)
            await event.reply(("✅ Добавлено в regexcut: " if added else "⚠️ Уже есть в regexcut: ") + arg)
            return True

        if command == "/unregexcut":
            removed, _ = remove_item_by_number(settings.regex_cut_phrases_file, int(arg))
            await event.reply(f"✅ Удалено из regexcut: {removed}" if removed else "⚠️ Не нашла такой номер")
            return True

        if command == "/listregexcut":
            await event.reply("✂️ Regex-cut правила:\n" + format_numbered(load_json_list(settings.regex_cut_phrases_file)))
            return True

        if command == "/addsource":
            if not arg:
                await event.reply("⚠️ Напиши так: /addsource @channel")
                return True
            added, _ = add_unique_item(settings.dynamic_sources_file, arg)
            await event.reply(("✅ Источник добавлен: " if added else "⚠️ Источник уже есть: ") + arg + "\nНачну учитывать его автоматически.")
            return True

        if command == "/removesource":
            removed, _ = remove_item_by_number(settings.dynamic_sources_file, int(arg))
            await event.reply(f"✅ Источник удалён: {removed}\nБольше не буду его учитывать." if removed else "⚠️ Не нашла такой номер")
            return True

        if command == "/listsources":
            await event.reply("📡 Источники:\n" + format_numbered(load_source_channels()))
            return True

    except ValueError:
        await event.reply("⚠️ Тут нужен номер из списка. Например: /uncut 2")
        return True
    except Exception as exc:
        logger.exception("Admin command failed: %s", exc)
        await event.reply(f"❌ Ошибка команды: {exc}")
        return True

    return False


@client.on(events.NewMessage)
async def new_message_listener(event):
    global cached_posts

    post = event.message
    raw_text = post.text or ""

    try:
        channel = await event.get_chat()
        channel_name = getattr(channel, "username", None) or getattr(channel, "title", "unknown")
        chat_id = getattr(channel, "id", None)
    except Exception:
        channel_name = "unknown"
        chat_id = None

    try:
        if raw_text.strip().startswith("/") and is_admin_channel(getattr(channel, "username", None), chat_id):
            handled = await handle_admin_command(event, raw_text.strip())
            if handled:
                return

        sources = load_source_channels()
        normalized_sources = {source.lower().lstrip("@") for source in sources}
        current_username = (getattr(channel, "username", None) or "").lower()
        current_id = str(chat_id)

        if current_username not in normalized_sources and current_id not in normalized_sources:
            return

        if not raw_text.strip():
            logger.info("Skip empty message from %s", channel_name)
            return
        if should_ignore(raw_text):
            logger.info("Skip ignored message from %s", channel_name)
            return

        text = clean_text(raw_text)
        if not text:
            logger.info("Skip empty cleaned message from %s", channel_name)
            return
        if is_duplicate(text, cached_posts):
            logger.info("Skip duplicate from %s", channel_name)
            return

        result = score_job(text)
        if result.score < settings.min_score_to_queue:
            logger.info("Skip weak job %s%% from %s: %s", result.score, channel_name, text[:80])
            cached_posts.append(text)
            save_post_cache(cached_posts)
            return

        photo_path = None
        if getattr(post, "photo", None) is not None or (post.media and hasattr(post.media, "photo")):
            photo_path = await post.download_media(file=str(settings.photos_dir))

        final_text = f"{result.header()}\n\n{text}"
        queue_path = save_to_queue(final_text, score=result.score, source_channel=str(channel_name), photo_path=photo_path)
        cached_posts.append(text)
        save_post_cache(cached_posts)
        logger.info("Queued %s%% job from %s -> %s", result.score, channel_name, queue_path.name)

    except Exception as exc:
        logger.exception("Error while processing message from %s: %s", channel_name, exc)


def main() -> None:
    global cached_posts
    settings.validate_parser()
    cached_posts = load_post_cache()
    logger.info("Python Job Radar parser started")
    logger.info("Sources: %s", ", ".join(load_source_channels()))
    with client:
        me = client.loop.run_until_complete(client.get_me())
        logger.info("Listening as %s", getattr(me, "username", None) or getattr(me, "first_name", "unknown"))
        client.run_until_disconnected()


if __name__ == "__main__":
    main()
