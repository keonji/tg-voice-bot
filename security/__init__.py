"""
Модуль безопасности для Telegram бота.
Включает авторизацию и валидацию пользовательского ввода.
"""

from .auth import (
    AuthManager,
    get_auth_manager,
    require_admin,
    check_admin_access,
    get_user_info_safe
)

from .validation import (
    InputValidator,
    validate_text_input
)

__all__ = [
    'AuthManager',
    'get_auth_manager', 
    'require_admin',
    'check_admin_access',
    'get_user_info_safe',
    'InputValidator',
    'validate_text_input'
]