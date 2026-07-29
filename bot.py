"""
Telegram бот транскрибации голосовых и видеосообщений.
"""

import os
import sys
import logging
from pathlib import Path
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, Defaults
from dotenv import load_dotenv
import pytz

# Импорты модулей бота
from security import get_auth_manager
from handlers import (
    start_command, status_command,
    voice_message_handler, video_note_message_handler
)
from utils import cleanup_temp_files

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Отключаем избыточное логирование
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)


class BotConfig:
    """Конфигурация бота."""

    def __init__(self):
        """Инициализация конфигурации."""
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не установлен в переменных окружения")

        self.AUDIO_TEMP_DIR = Path(os.getenv("AUDIO_TEMP_DIR", "./temp_audio"))
        self.MODELS_DIR = Path(os.getenv("MODELS_DIR", "./models"))

        for directory in [self.AUDIO_TEMP_DIR, self.MODELS_DIR]:
            directory.mkdir(parents=True, exist_ok=True)

        self.TIME_ZONE = os.getenv("TIME_ZONE", "Europe/Moscow")
        self.CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "24"))

        logger.info("Конфигурация бота загружена")


class TelegramBot:
    """Основной класс Telegram бота."""

    def __init__(self):
        """Инициализация бота."""
        self.config = BotConfig()
        self.application = None
        self.auth_manager = None

    async def _post_init_callback(self, application):
        """Callback, вызываемый после инициализации приложения."""
        await self._startup_check()

    async def _startup_check(self):
        """Проверка готовности системы при запуске."""
        logger.info("🚀 Проверка готовности системы...")

        self.auth_manager = get_auth_manager()
        admin_count = self.auth_manager.get_admin_count()
        if admin_count == 0:
            logger.warning("⚠️ Не настроены администраторы бота!")
            logger.warning("Добавьте в .env: ADMIN_USER_IDS=123456789,987654321")
        else:
            logger.info(f"✅ Настроено администраторов: {admin_count}")

        logger.info("🎯 Система готова к работе!")

    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений."""
        app = self.application

        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("status", status_command))

        app.add_handler(MessageHandler(filters.VOICE, voice_message_handler))
        app.add_handler(MessageHandler(filters.VIDEO_NOTE, video_note_message_handler))

        logger.info("Обработчики команд и сообщений настроены")

    def _setup_jobs(self):
        """Настройка периодических задач."""
        job_queue = self.application.job_queue

        job_queue.run_repeating(
            self._cleanup_task,
            interval=self.config.CLEANUP_INTERVAL * 3600,
            first=300,
            name="cleanup_temp_files"
        )
        logger.info(f"Очистка временных файлов настроена (каждые {self.config.CLEANUP_INTERVAL} часов)")

    async def _cleanup_task(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодическая задача очистки временных файлов."""
        try:
            logger.info("Запуск очистки временных файлов")

            removed_count = cleanup_temp_files(
                self.config.AUDIO_TEMP_DIR,
                max_age_hours=self.config.CLEANUP_INTERVAL,
                file_patterns=['*.ogg', '*.wav', '*.mp3', '*.tmp']
            )

            if removed_count > 0:
                logger.info(f"Очищено {removed_count} временных файлов")

            logger.info("Очистка временных файлов завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке временных файлов: {e}")

    async def _shutdown_handler(self, application):
        """Обработчик корректного завершения работы."""
        logger.info("🛑 Завершение работы бота...")

        try:
            cleanup_temp_files(self.config.AUDIO_TEMP_DIR, max_age_hours=0)
            logger.info("✅ Бот корректно завершил работу")

        except Exception as e:
            logger.error(f"Ошибка при завершении работы: {e}")

    def run_sync(self):
        """Синхронный запуск бота."""
        try:
            local_tz = pytz.timezone(self.config.TIME_ZONE)
            defaults = Defaults(parse_mode='HTML', tzinfo=local_tz)
            builder = Application.builder().token(self.config.BOT_TOKEN).defaults(defaults)

            builder.post_init(self._post_init_callback)
            builder.post_shutdown(self._shutdown_handler)

            self.application = builder.build()

            self._setup_handlers()
            self._setup_jobs()

            logger.info("🤖 Запуск Telegram бота...")
            self.application.run_polling(
                allowed_updates=["message"],
                drop_pending_updates=True
            )

        except KeyboardInterrupt:
            logger.info("Получен сигнал прерывания")
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            raise


def main():
    """Главная функция."""
    try:
        bot = TelegramBot()
        bot.run_sync()
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Программа прервана пользователем")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        sys.exit(1)
