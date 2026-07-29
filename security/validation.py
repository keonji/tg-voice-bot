"""
Модуль валидации пользовательского ввода.
Обеспечивает безопасную обработку всех входящих данных.
"""

import re
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class InputValidator:
    """Валидатор пользовательского ввода."""

    # Максимальная длина текста по умолчанию
    MAX_TEXT_LENGTH = 10000

    # Опасные паттерны, которые нужно блокировать
    DANGEROUS_PATTERNS = [
        re.compile(r'<script[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL),
        re.compile(r'javascript:', re.IGNORECASE),
        re.compile(r'on\w+\s*=', re.IGNORECASE),
        re.compile(r'eval\s*\(', re.IGNORECASE),
        re.compile(r'exec\s*\(', re.IGNORECASE),
        re.compile(r'system\s*\(', re.IGNORECASE),
        re.compile(r'subprocess\s*\.', re.IGNORECASE),
        re.compile(r'os\s*\.', re.IGNORECASE),
        re.compile(r'__import__', re.IGNORECASE),
        re.compile(r'\.\./', re.IGNORECASE),  # Path traversal
        re.compile(r'\\\\', re.IGNORECASE),   # Windows path traversal
    ]
    
    @classmethod
    def validate_text(cls, text: str, max_length: Optional[int] = None, 
                     allow_empty: bool = False, context: str = "text") -> Dict[str, Any]:
        """
        Валидирует текстовый ввод.
        
        Args:
            text: Текст для валидации
            max_length: Максимальная длина (по умолчанию MAX_TEXT_LENGTH)
            allow_empty: Разрешить пустой текст
            context: Контекст валидации для логирования
            
        Returns:
            Словарь с результатами валидации:
            {
                'valid': bool,
                'sanitized': str,
                'errors': List[str],
                'warnings': List[str]
            }
        """
        if max_length is None:
            max_length = cls.MAX_TEXT_LENGTH
        
        result = {
            'valid': True,
            'sanitized': '',
            'errors': [],
            'warnings': []
        }
        
        # Проверка на None
        if text is None:
            result['valid'] = False
            result['errors'].append("Текст не может быть None")
            return result
        
        # Проверка типа
        if not isinstance(text, str):
            result['valid'] = False
            result['errors'].append(f"Ожидается строка, получен {type(text)}")
            return result
        
        # Проверка на пустоту
        if not text.strip() and not allow_empty:
            result['valid'] = False
            result['errors'].append("Текст не может быть пустым")
            return result
        
        # Проверка длины
        if len(text) > max_length:
            result['valid'] = False
            result['errors'].append(f"Текст слишком длинный: {len(text)} > {max_length}")
            return result
        
        # Проверка на опасные паттерны
        for pattern in cls.DANGEROUS_PATTERNS:
            if pattern.search(text):
                result['valid'] = False
                result['errors'].append(f"Обнаружен опасный паттерн в тексте")
                logger.warning(f"Опасный паттерн в {context}: {pattern.pattern}")
                return result
        
        # Базовая санитизация
        sanitized = text.strip()
        
        # Удаляем потенциально опасные символы
        sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', sanitized)
        
        # Ограничиваем количество повторяющихся символов
        sanitized = re.sub(r'(.)\1{10,}', r'\1\1\1', sanitized)
        
        result['sanitized'] = sanitized
        
        # Предупреждения
        if len(sanitized) != len(text):
            result['warnings'].append("Текст был изменен при санитизации")
        
        if len(text) > max_length * 0.8:
            result['warnings'].append("Текст близок к максимальной длине")
        
        return result

    @classmethod
    def sanitize_for_html(cls, text: str) -> str:
        """
        Санитизирует текст для безопасного использования в HTML.
        
        Args:
            text: Текст для санитизации
            
        Returns:
            Санитизированный текст
        """
        if not isinstance(text, str):
            return str(text)
        
        # Экранируем HTML символы
        html_escape_table = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#x27;",
        }
        
        sanitized = text
        for char, escape in html_escape_table.items():
            sanitized = sanitized.replace(char, escape)
        
        return sanitized
    
    @classmethod
    def validate_and_sanitize_message(cls, text: str, max_length: int = 4000) -> str:
        """
        Быстрая валидация и санитизация сообщения для Telegram.
        
        Args:
            text: Текст сообщения
            max_length: Максимальная длина сообщения
            
        Returns:
            Санитизированный текст
            
        Raises:
            ValueError: Если текст не прошел валидацию
        """
        result = cls.validate_text(text, max_length=max_length, context="telegram_message")
        
        if not result['valid']:
            error_msg = "; ".join(result['errors'])
            raise ValueError(f"Некорректное сообщение: {error_msg}")
        
        return result['sanitized']


# Глобальные функции для удобства использования
def validate_text_input(text: str, max_length: Optional[int] = None) -> str:
    """
    Быстрая валидация текстового ввода.
    
    Args:
        text: Текст для валидации
        max_length: Максимальная длина
        
    Returns:
        Санитизированный текст
        
    Raises:
        ValueError: Если текст не прошел валидацию
    """
    return InputValidator.validate_and_sanitize_message(text, max_length or InputValidator.MAX_TEXT_LENGTH)