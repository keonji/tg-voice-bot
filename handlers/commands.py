"""
Обработчики команд Telegram бота.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from security import require_admin, get_user_info_safe
from services.transcription_service import get_transcription_service

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start – выводит справку по командам."""
    try:
        user_info = get_user_info_safe(update)
        logger.info(f"Команда /start от пользователя {user_info['user_id']} (@{user_info['username']})")

        text = (
            "🔊 Добро пожаловать в бот транскрибации!\n\n"
            "Просто отправьте боту:\n"
            "🎙️ Голосовое сообщение → автоматическая транскрибация\n"
            "🎥 Видеосообщение (кружок) → извлечение аудио + транскрибация\n"
        )

        if user_info['is_admin']:
            text += "\nДоступные команды:\n"
            text += "• /status — статус транскрибации (только для администраторов)\n"

        await update.message.reply_text(text)

    except Exception as e:
        logger.error(f"Ошибка в команде /start: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке команды. Попробуйте позже."
        )


@require_admin
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status — показывает состояние сервиса транскрибации."""
    try:
        user_info = get_user_info_safe(update)
        logger.info(f"Административная команда /status от пользователя {user_info['user_id']} (@{user_info['username']})")

        service = get_transcription_service()

        status_text = "📊 Статус транскрибации:\n\n"
        status_text += f"🧠 Модель: {service.whisper_model}\n"
        status_text += f"⚙️ Движок: {'faster-whisper' if service.use_faster_whisper else 'openai-whisper'}\n"
        status_text += f"🖥️ Устройство: {service.whisper_device} ({service.compute_type})\n"
        status_text += f"🌐 Язык: {service.transcription_language}\n"
        status_text += f"✍️ Восстановление пунктуации: {'вкл' if service.add_punctuation else 'выкл'}\n"

        # Модель грузится лениво — при первом голосовом сообщении
        loaded = service.is_model_loaded()
        status_text += f"\n{'✅ Модель загружена в память' if loaded else 'ℹ️ Модель ещё не загружена (загрузится при первом сообщении)'}"

        await update.message.reply_text(status_text)

    except Exception as e:
        logger.error(f"Ошибка в команде /status: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке статуса."
        )
