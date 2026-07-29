"""
Тесты утилит.
"""

import pytest
from pathlib import Path
import tempfile

from utils.file_utils import cleanup_temp_files, safe_create_temp_file, safe_remove_file

from handlers.messages import _split_text


class TestSplitText:
    """Тесты разбиения длинной транскрибации на части."""

    def test_split_text_short(self):
        """Короткий текст не разбивается."""
        text = "Короткое сообщение"
        assert _split_text(text, 1000) == [text]

    def test_split_text_long(self):
        """Длинный текст разбивается по границам предложений."""
        text = "Часть 1. " * 100 + "Часть 2. " * 100
        result = _split_text(text, 500)
        assert len(result) > 1
        for part in result:
            assert len(part) <= 500

    def test_split_text_preserves_content(self):
        """При разбиении не теряются слова."""
        text = "Слово " * 500
        result = _split_text(text, 400)
        assert "".join(result).replace(" ", "") == text.replace(" ", "")


class TestFileUtils:
    """Тесты утилит для файлов."""
    
    def test_safe_create_temp_file(self):
        """Тест создания временного файла."""
        temp_file = safe_create_temp_file(suffix='.test', prefix='test_')
        
        assert temp_file.exists()
        assert temp_file.name.endswith('.test')
        assert 'test_' in temp_file.name
        
        # Удаляем файл
        temp_file.unlink()
    
    def test_safe_remove_file(self):
        """Тест удаления файла."""
        # Создаем временный файл
        temp_file = safe_create_temp_file()
        assert temp_file.exists()
        
        # Удаляем файл
        result = safe_remove_file(temp_file)
        assert result is True
        assert not temp_file.exists()
        
        # Попытка удалить несуществующий файл
        result = safe_remove_file(temp_file)
        assert result is True  # Должно вернуть True даже если файл не существует
    
    def test_cleanup_temp_files(self):
        """Тест очистки временных файлов."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Создаем несколько тестовых файлов
            test_files = []
            for i in range(3):
                test_file = temp_path / f"test_{i}.tmp"
                test_file.write_text("test content")
                test_files.append(test_file)
            
            # Проверяем что файлы созданы
            for test_file in test_files:
                assert test_file.exists()
            
            # Очищаем файлы (возраст 0 часов = удалить все)
            removed_count = cleanup_temp_files(
                temp_path, 
                max_age_hours=0, 
                file_patterns=['*.tmp']
            )
            
            assert removed_count == 3
            
            # Проверяем что файлы удалены
            for test_file in test_files:
                assert not test_file.exists()


if __name__ == "__main__":
    pytest.main([__file__])