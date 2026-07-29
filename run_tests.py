#!/usr/bin/env python3
"""
Скрипт для запуска тестов и базовых проверок целостности проекта.
"""

import sys
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Модули, которые должны компилироваться и импортироваться
SOURCE_FILES = [
    "bot.py",
    "security/__init__.py",
    "security/auth.py",
    "security/validation.py",
    "handlers/__init__.py",
    "handlers/commands.py",
    "handlers/messages.py",
    "services/__init__.py",
    "services/transcription_service.py",
    "utils/__init__.py",
    "utils/file_utils.py",
]

# Публичные точки входа модулей — ловим рассинхрон __init__ и реализации
PUBLIC_IMPORTS = [
    "from security import get_auth_manager, InputValidator, validate_text_input",
    "from handlers import start_command, status_command, voice_message_handler",
    "from services import get_transcription_service",
    "from utils import cleanup_temp_files",
]

TEST_FILES = [
    "tests/test_security.py",
    "tests/test_utils.py",
]


def run_command(cmd, description):
    """Запускает команду и возвращает True при успехе."""
    logger.info(f"🔄 {description}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info(f"✅ {description}")
            if result.stdout.strip():
                print(result.stdout)
            return True

        logger.error(f"❌ {description}")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"⏰ {description} — превышено время ожидания")
        return False
    except Exception as e:
        logger.error(f"💥 {description} — исключение: {e}")
        return False


def check_syntax():
    """Проверяет синтаксис исходных файлов."""
    logger.info("📊 Проверка синтаксиса...")
    all_ok = True
    for py_file in SOURCE_FILES:
        if not Path(py_file).exists():
            logger.error(f"❌ Файл не найден: {py_file}")
            all_ok = False
            continue
        if not run_command(f"{sys.executable} -m py_compile {py_file}", f"Синтаксис {py_file}"):
            all_ok = False
    return all_ok


def check_imports():
    """Проверяет, что публичные API модулей импортируются."""
    logger.info("🔗 Проверка импортов...")
    all_ok = True
    for import_stmt in PUBLIC_IMPORTS:
        cmd = f'{sys.executable} -c "{import_stmt}"'
        if not run_command(cmd, f"Импорт: {import_stmt}"):
            all_ok = False
    return all_ok


def run_tests():
    """Запускает pytest."""
    logger.info("🛡️ Запуск тестов...")
    existing = [f for f in TEST_FILES if Path(f).exists()]
    if not existing:
        logger.warning("⚠️ Файлы тестов не найдены")
        return True
    return run_command(f"{sys.executable} -m pytest {' '.join(existing)} -v", "pytest")


def main():
    """Главная функция."""
    logger.info("🚀 Проверка проекта tg-ai-responder")
    logger.info("=" * 60)

    checks = [
        ("Синтаксис", check_syntax),
        ("Импорты", check_imports),
        ("Тесты", run_tests),
    ]

    failed = [name for name, check in checks if not check()]

    logger.info("=" * 60)
    if not failed:
        logger.info(f"🎉 Все проверки пройдены ({len(checks)}/{len(checks)})")
        return 0

    logger.error(f"❌ Не пройдено проверок: {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("⚠️ Прервано пользователем")
        sys.exit(1)
