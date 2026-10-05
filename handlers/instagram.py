"""
Пересылка медиа из ссылок на Instagram в чат.
"""

import asyncio
import logging
from contextlib import ExitStack
from typing import List

from telegram import InputMediaPhoto, InputMediaVideo, Message, MessageEntity, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from security import check_admin_access, get_user_info_safe
from services.chat_settings import ChatSettings, get_chat_settings
from services.instagram_service import (
    InstagramDownloadError, MediaItem, extract_instagram_urls, get_instagram_service,
)
from handlers.messages import _keep_typing

logger = logging.getLogger(__name__)

MEDIA_GROUP_LIMIT = 10

UPLOAD_TIMEOUTS = {"write_timeout": 300, "read_timeout": 60}


async def _can_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """В личке настройкой управляет собеседник, в группе — админы чата и бота."""
    chat = update.effective_chat
    user = update.effective_user
    message = update.message

    if chat.type == ChatType.PRIVATE:
        return True
    # Анонимный админ группы пишет от имени самого чата
    if message and message.sender_chat and message.sender_chat.id == chat.id:
        return True
    if not user:
        return False
    if check_admin_access(user.id):
        return True
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def instagram_enabled(chat) -> bool:
    """В личке включено по умолчанию, в группах — только после /instagram on."""
    return get_chat_settings().is_enabled(
        chat.id, ChatSettings.INSTAGRAM, default=chat.type == ChatType.PRIVATE
    )


async def instagram_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/instagram [on|off] — включает, выключает или показывает пересылку медиа из Instagram."""
    try:
        chat_id = update.effective_chat.id
        settings = get_chat_settings()
        arg = context.args[0].lower() if context.args else ""

        if arg not in ("on", "off"):
            state = "включена" if instagram_enabled(update.effective_chat) else "выключена"
            await update.message.reply_text(
                f"📸 Загрузка медиа из Instagram в этом чате {state}.\n\n"
                "/instagram on — включить\n/instagram off — выключить"
            )
            return

        if not await _can_manage(update, context):
            await update.message.reply_text("🔒 Менять настройку могут только администраторы чата.")
            return

        enabled = arg == "on"
        settings.set_enabled(chat_id, ChatSettings.INSTAGRAM, enabled)
        user_info = get_user_info_safe(update)
        logger.info(f"Instagram {'включён' if enabled else 'выключен'} в чате {chat_id} пользователем {user_info['user_id']}")

        await update.message.reply_text(
            "✅ Загрузка медиа из Instagram включена." if enabled
            else "☑️ Загрузка медиа из Instagram выключена."
        )

    except Exception as e:
        logger.error(f"Ошибка в команде /instagram: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке команды.")


def _message_links_text(message: Message) -> str:
    """Текст/подпись сообщения плюс скрытые URL из text_link-сущностей."""
    parts = [message.text or message.caption or ""]
    entities = {**message.parse_entities([MessageEntity.TEXT_LINK]),
                **message.parse_caption_entities([MessageEntity.TEXT_LINK])}
    parts += [entity.url for entity in entities if entity.url]
    return "\n".join(parts)


async def instagram_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ищет ссылки на посты Instagram и отвечает их медиа, если функция включена в чате."""
    message = update.message
    if not message or not instagram_enabled(message.chat):
        return

    for url in extract_instagram_urls(_message_links_text(message)):
        await _process_post(message, context, url)


async def _process_post(message: Message, context: ContextTypes.DEFAULT_TYPE, url: str):
    service = get_instagram_service()
    logger.info(f"Instagram: скачивание {url} для чата {message.chat_id}")

    stop_action = asyncio.Event()
    action_task = asyncio.create_task(
        _keep_typing(context.bot, message.chat_id, stop_action, action="upload_video")
    )
    work_dir = None
    try:
        work_dir, items = await service.download(url)
        await _send_media(message, items)
        logger.info(f"Instagram: {url} отправлен ({len(items)} файлов)")

    except InstagramDownloadError as e:
        logger.warning(f"Instagram: не удалось скачать {url}: {e}")
        await message.reply_text(
            "⚠️ Не удалось скачать пост из Instagram: он удалён, закрыт или требует входа.",
            quote=True,
        )
    except Exception as e:
        logger.error(f"Instagram: ошибка отправки {url}: {e}")
        await message.reply_text("❌ Не удалось загрузить медиа из Instagram в чат.", quote=True)
    finally:
        stop_action.set()
        await action_task
        # Медиа на диске не храним: после отправки (или неудачи) директория больше не нужна
        if work_dir:
            service.cleanup(work_dir)


def _numbers(items: List[MediaItem], predicate) -> str:
    return ", ".join(f"№{i}" for i, item in enumerate(items, 1) if predicate(item))


def build_caption(items: List[MediaItem]) -> str:
    """Предупреждения о видео без звука, сжатых и пропущенных файлах."""
    lines = []
    single = len(items) == 1
    sent = [item for item in items if item.path]

    if any(item.is_video and not item.has_audio for item in sent):
        if single:
            lines.append("🔇 Видео без звука: Instagram не отдаёт для него аудиодорожку.")
        else:
            numbers = _numbers(items, lambda it: it.path and it.is_video and not it.has_audio)
            lines.append(f"🔇 Без звука: видео {numbers} — Instagram не отдаёт для них аудиодорожку.")

    if any(item.compressed for item in sent):
        if single:
            lines.append("🗜 Сжато, чтобы уложиться в лимит Telegram — качество ниже оригинала.")
        else:
            lines.append(f"🗜 Сжаты под лимит Telegram: {_numbers(items, lambda it: it.path and it.compressed)}.")

    if len(sent) < len(items):
        if single:
            lines.append("⚠️ Не удалось подготовить файл к отправке в Telegram.")
        else:
            lines.append(f"⚠️ Не удалось отправить: {_numbers(items, lambda it: not it.path)}.")
    return "\n".join(lines)


def chunk_media(items: list, limit: int = MEDIA_GROUP_LIMIT) -> List[list]:
    """Делит на альбомы поровну: альбом из одного элемента Bot API не принимает."""
    groups = -(-len(items) // limit)
    size, extra = divmod(len(items), groups)
    chunks, start = [], 0
    for g in range(groups):
        end = start + size + (1 if g < extra else 0)
        chunks.append(items[start:end])
        start = end
    return chunks


async def _send_media(message: Message, items: List[MediaItem]):
    sendable = [item for item in items if item.path]
    caption = build_caption(items) or None
    if not sendable:
        await message.reply_text(caption, quote=True)
        return

    with ExitStack() as stack:
        def open_file(item: MediaItem):
            return stack.enter_context(open(item.path, "rb"))

        if len(sendable) == 1:
            item = sendable[0]
            if item.is_video:
                await message.reply_video(
                    video=open_file(item), caption=caption, width=item.width, height=item.height,
                    duration=item.duration, supports_streaming=True, quote=True, **UPLOAD_TIMEOUTS,
                )
            else:
                await message.reply_photo(photo=open_file(item), caption=caption, quote=True, **UPLOAD_TIMEOUTS)
            return

        first = True
        for chunk in chunk_media(sendable):
            media = []
            for item in chunk:
                item_caption = caption if first else None
                first = False
                if item.is_video:
                    media.append(InputMediaVideo(
                        open_file(item), caption=item_caption, width=item.width, height=item.height,
                        duration=item.duration, supports_streaming=True,
                    ))
                else:
                    media.append(InputMediaPhoto(open_file(item), caption=item_caption))
            await message.reply_media_group(media=media, quote=True, **UPLOAD_TIMEOUTS)
