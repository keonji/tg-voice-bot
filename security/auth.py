"""
Модуль авторизации для Telegram бота.
Обеспечивает проверку прав доступа к административным функциям.
"""

import os
import logging
from typing import Set, Optional
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class AuthManager:
    """Менеджер авторизации для проверки прав пользователей."""
    
    def __init__(self):
        """Инициализация менеджера авторизации."""
        # Загружаем список администраторов из переменных окружения
        admin_ids_str = os.getenv("ADMIN_USER_IDS", "")
        self.admin_ids: Set[int] = set()
        
        if admin_ids_str:
            try:
                # Парсим список ID администраторов
                admin_ids = [int(uid.strip()) for uid in admin_ids_str.split(",") if uid.strip()]
                self.admin_ids = set(admin_ids)
                logger.info(f"Загружено {len(self.admin_ids)} администраторов")
            except ValueError as e:
                logger.error(f"Ошибка парсинга ADMIN_USER_IDS: {e}")
        
        if not self.admin_ids:
            logger.warning("⚠️ Не настроены администраторы! Все административные команды будут недоступны.")
            logger.warning("Добавьте в .env: ADMIN_USER_IDS=123456789,987654321")
    
    def is_admin(self, user_id: int) -> bool:
        """
        Проверяет, является ли пользователь администратором.
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            True если пользователь администратор, False иначе
        """
        return user_id in self.admin_ids
    
    def get_admin_count(self) -> int:
        """Возвращает количество администраторов."""
        return len(self.admin_ids)


# Глобальный экземпляр менеджера авторизации
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Возвращает глобальный экземпляр менеджера авторизации."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


def require_admin(func):
    """
    Декоратор для проверки административных прав.
    
    Использование:
    @require_admin
    async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Код команды, доступной только администраторам
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            await update.message.reply_text("❌ Ошибка: не удалось определить пользователя")
            return
        
        auth_manager = get_auth_manager()
        
        if not auth_manager.is_admin(user.id):
            logger.warning(f"Попытка доступа к административной команде от пользователя {user.id} (@{user.username})")
            await update.message.reply_text(
                "🔒 Доступ запрещен\n\n"
                "Эта команда доступна только администраторам бота.\n"
                "Обратитесь к администратору для получения доступа."
            )
            return
        
        # Логируем использование административной команды
        logger.info(f"Административная команда выполнена пользователем {user.id} (@{user.username})")
        
        # Выполняем оригинальную функцию
        return await func(update, context)
    
    return wrapper


def check_admin_access(user_id: int) -> bool:
    """
    Простая функция для проверки административных прав.
    
    Args:
        user_id: ID пользователя Telegram
        
    Returns:
        True если пользователь администратор, False иначе
    """
    auth_manager = get_auth_manager()
    return auth_manager.is_admin(user_id)


def get_user_info_safe(update: Update) -> dict:
    """
    Безопасно извлекает информацию о пользователе.
    
    Args:
        update: Telegram Update объект
        
    Returns:
        Словарь с информацией о пользователе
    """
    user = update.effective_user
    chat = update.effective_chat
    
    return {
        'user_id': user.id if user else None,
        'username': user.username if user else None,
        'first_name': user.first_name if user else None,
        'chat_id': chat.id if chat else None,
        'chat_type': chat.type if chat else None,
        'is_admin': check_admin_access(user.id) if user else False
    }