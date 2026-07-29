"""
Обработчики сообщений Telegram бота.
"""

import asyncio
import logging
from html import escape
from telegram import Update
from telegram.ext import ContextTypes

from security import validate_text_input, get_user_info_safe
from services.transcription_service import get_transcription_service

logger = logging.getLogger(__name__)


async def _keep_typing(bot, chat_id: int, stop_event: asyncio.Event):
    """Периодически шлёт 'typing' пока идёт длинная операция (TG скрывает индикатор через ~5с)."""
    try:
        while not stop_event.is_set():
            try:
                await bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        pass


def _display_name(user) -> str:
    """Отображаемое имя автора для подписи к транскрибации."""
    parts = [user.first_name, user.last_name]
    name = " ".join(p for p in parts if p).strip()
    return name or (f"@{user.username}" if user.username else f"user{user.id}")


async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений."""
    try:
        user_info = get_user_info_safe(update)
        logger.info(f"Голосовое сообщение от пользователя {user_info['user_id']} (@{user_info['username']})")

        await context.bot.send_chat_action(update.message.chat_id, "typing")

        await _process_audio_message(update, context, update.message.voice)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке голосового сообщения."
        )


async def video_note_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик видеосообщений (кружков)."""
    try:
        user_info = get_user_info_safe(update)
        logger.info(f"Видеосообщение от пользователя {user_info['user_id']} (@{user_info['username']})")

        await context.bot.send_chat_action(update.message.chat_id, "typing")

        await _process_audio_message(update, context, update.message.video_note)

    except Exception as e:
        logger.error(f"Ошибка обработки видеосообщения: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке видеосообщения."
        )


async def _process_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_file):
    """Внутренняя функция для обработки аудио/видео сообщений."""
    try:
        user = update.message.from_user

        transcription_service = get_transcription_service()

        # Длинные голосовые могут транскрибироваться минутами — держим typing-индикатор,
        # иначе Telegram скроет его через ~5 секунд и пользователь думает что бот завис.
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(_keep_typing(context.bot, update.message.chat_id, stop_typing))
        try:
            transcription_result = await transcription_service.transcribe_telegram_audio(
                bot=context.bot,
                audio_file=audio_file
            )
        finally:
            stop_typing.set()
            try:
                await typing_task
            except Exception:
                pass

        if not transcription_result or not transcription_result.get('text'):
            await update.message.reply_text(
                "😔 Не удалось распознать аудио. Попробуйте записать сообщение заново."
            )
            return

        # Файл слишком большой для Telegram getFile (лимит 20 МБ для бот-API)
        if transcription_result.get('too_large'):
            await update.message.reply_text(
                f"⚠️ {transcription_result['text']}"
            )
            return

        transcribed_text = transcription_result['text']

        try:
            transcribed_text = validate_text_input(transcribed_text, max_length=20000)
        except ValueError as e:
            logger.warning(f"Некорректная транскрибация: {e}")
            await update.message.reply_text(
                "❌ Транскрибированный текст содержит недопустимые символы."
            )
            return

        author_name = _display_name(user)

        # Telegram message limit is 4096 chars, leave room for header and HTML tags
        MAX_LEN = 3800
        safe_name = escape(author_name)
        chunks = _split_text(transcribed_text, MAX_LEN)
        total = len(chunks)
        for idx, chunk in enumerate(chunks, 1):
            header = f"🎤 {safe_name}" if total == 1 else f"🎤 {safe_name} (часть {idx}/{total})"
            await update.message.reply_text(
                f"{header}:\n<blockquote expandable>{escape(chunk)}</blockquote>",
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error(f"Ошибка обработки аудио сообщения: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке аудио сообщения."
        )


def _split_text(text: str, max_length: int) -> list:
    """Разбивает текст по предложениям или пробелам, не превышая max_length."""
    if len(text) <= max_length:
        return [text]
    parts = []
    remaining = text
    while len(remaining) > max_length:
        cut = max_length
        sentence = remaining.rfind('. ', 0, max_length)
        if sentence > max_length // 2:
            cut = sentence + 2
        else:
            space = remaining.rfind(' ', 0, max_length)
            if space > max_length // 2:
                cut = space + 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts
