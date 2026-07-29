"""
Тесты модуля безопасности.
"""

import pytest
import os
from unittest.mock import patch, MagicMock

from security.auth import AuthManager, get_auth_manager, check_admin_access
from security.validation import InputValidator, validate_text_input


class TestAuthManager:
    """Тесты менеджера авторизации."""
    
    def test_init_with_admin_ids(self):
        """Тест инициализации с ID администраторов."""
        with patch.dict(os.environ, {'ADMIN_USER_IDS': '123,456,789'}):
            auth_manager = AuthManager()
            assert auth_manager.admin_ids == {123, 456, 789}
            assert auth_manager.get_admin_count() == 3
    
    def test_init_without_admin_ids(self):
        """Тест инициализации без ID администраторов."""
        with patch.dict(os.environ, {}, clear=True):
            auth_manager = AuthManager()
            assert auth_manager.admin_ids == set()
            assert auth_manager.get_admin_count() == 0
    
    def test_is_admin(self):
        """Тест проверки администратора."""
        with patch.dict(os.environ, {'ADMIN_USER_IDS': '123,456'}):
            auth_manager = AuthManager()
            assert auth_manager.is_admin(123) is True
            assert auth_manager.is_admin(456) is True
            assert auth_manager.is_admin(789) is False


class TestInputValidator:
    """Тесты валидатора ввода."""
    
    def test_validate_text_valid(self):
        """Тест валидации корректного текста."""
        result = InputValidator.validate_text("Привет, мир!")
        assert result['valid'] is True
        assert result['sanitized'] == "Привет, мир!"
        assert len(result['errors']) == 0
    
    def test_validate_text_none(self):
        """Тест валидации None."""
        result = InputValidator.validate_text(None)
        assert result['valid'] is False
        assert "не может быть None" in result['errors'][0]
    
    def test_validate_text_empty(self):
        """Тест валидации пустого текста."""
        result = InputValidator.validate_text("", allow_empty=False)
        assert result['valid'] is False
        assert "не может быть пустым" in result['errors'][0]
    
    def test_validate_text_too_long(self):
        """Тест валидации слишком длинного текста."""
        long_text = "a" * 10001
        result = InputValidator.validate_text(long_text, max_length=1000)
        assert result['valid'] is False
        assert "слишком длинный" in result['errors'][0]
    
    def test_validate_text_dangerous_patterns(self):
        """Тест валидации опасных паттернов."""
        dangerous_texts = [
            "<script>alert('xss')</script>",
            "javascript:alert(1)",
            "eval(malicious_code)",
            "../../../etc/passwd",
        ]
        
        for text in dangerous_texts:
            result = InputValidator.validate_text(text)
            assert result['valid'] is False
            assert "опасный паттерн" in result['errors'][0]

    def test_sanitize_for_html(self):
        """Тест санитизации HTML."""
        text = '<script>alert("xss")</script>'
        sanitized = InputValidator.sanitize_for_html(text)
        assert "<script>" not in sanitized
        assert "&lt;script&gt;" in sanitized
    
    def test_validate_and_sanitize_message(self):
        """Тест быстрой валидации сообщения."""
        text = "Нормальное сообщение"
        result = InputValidator.validate_and_sanitize_message(text)
        assert result == text
        
        with pytest.raises(ValueError):
            InputValidator.validate_and_sanitize_message("<script>alert(1)</script>")


def test_validate_text_input():
    """Тест глобальной функции валидации."""
    result = validate_text_input("Тест")
    assert result == "Тест"
    
    with pytest.raises(ValueError):
        validate_text_input("<script>alert(1)</script>")


def test_check_admin_access():
    """Тест глобальной функции проверки доступа."""
    with patch.dict(os.environ, {'ADMIN_USER_IDS': '123'}):
        # Сбрасываем глобальный экземпляр
        import security.auth
        security.auth._auth_manager = None
        
        assert check_admin_access(123) is True
        assert check_admin_access(456) is False