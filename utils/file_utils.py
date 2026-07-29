"""
Утилиты для безопасной работы с файлами.
"""

import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


def cleanup_temp_files(temp_dir: Path, max_age_hours: int = 24,
                      file_patterns: Optional[List[str]] = None) -> int:
    """
    Очищает временные файлы старше указанного возраста.
    
    Args:
        temp_dir: Директория с временными файлами
        max_age_hours: Максимальный возраст файлов в часах
        file_patterns: Список паттернов файлов для очистки (например, ['*.wav', '*.tmp'])
        
    Returns:
        Количество удаленных файлов
    """
    if not temp_dir.exists():
        return 0
    
    if file_patterns is None:
        file_patterns = ['*']
    
    now = datetime.now()
    cleanup_threshold = timedelta(hours=max_age_hours)
    removed_count = 0
    total_size = 0
    
    try:
        for pattern in file_patterns:
            for file_path in temp_dir.glob(pattern):
                if not file_path.is_file():
                    continue
                
                try:
                    # Проверяем возраст файла
                    file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if (now - file_time) >= cleanup_threshold:
                        file_size = file_path.stat().st_size
                        total_size += file_size
                        
                        # Удаляем файл
                        file_path.unlink()
                        removed_count += 1
                        
                        logger.debug(f"Удален временный файл: {file_path.name} ({file_size} байт)")
                        
                except Exception as e:
                    logger.warning(f"Ошибка при удалении файла {file_path}: {e}")
        
        if removed_count > 0:
            size_mb = total_size / (1024 * 1024)
            logger.info(f"Очистка временных файлов: удалено {removed_count} файлов, освобождено {size_mb:.2f} MB")
        else:
            logger.debug("Временные файлы для очистки не найдены")
            
    except Exception as e:
        logger.error(f"Ошибка очистки временных файлов в {temp_dir}: {e}")
    
    return removed_count


def safe_create_temp_file(suffix: str = '.tmp', prefix: str = 'bot_', 
                         dir_path: Optional[Path] = None) -> Path:
    """
    Безопасно создает временный файл.
    
    Args:
        suffix: Суффикс файла
        prefix: Префикс файла
        dir_path: Директория для создания файла
        
    Returns:
        Путь к созданному временному файлу
        
    Raises:
        IOError: При ошибке создания файла
    """
    try:
        if dir_path:
            dir_path.mkdir(parents=True, exist_ok=True)
            temp_dir = str(dir_path)
        else:
            temp_dir = None
        
        # Создаем временный файл
        temp_file = tempfile.NamedTemporaryFile(
            suffix=suffix,
            prefix=prefix,
            dir=temp_dir,
            delete=False
        )
        temp_path = Path(temp_file.name)
        temp_file.close()
        
        logger.debug(f"Создан временный файл: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"Ошибка создания временного файла: {e}")
        raise IOError(f"Не удалось создать временный файл: {e}")


def safe_remove_file(file_path: Path, ignore_errors: bool = True) -> bool:
    """
    Безопасно удаляет файл.
    
    Args:
        file_path: Путь к файлу
        ignore_errors: Игнорировать ошибки удаления
        
    Returns:
        True если файл удален успешно, False иначе
    """
    try:
        if file_path.exists():
            file_path.unlink()
            logger.debug(f"Удален файл: {file_path}")
            return True
        return True  # Файл не существует - считаем успехом
        
    except Exception as e:
        if ignore_errors:
            logger.warning(f"Не удалось удалить файл {file_path}: {e}")
            return False
        else:
            raise IOError(f"Ошибка удаления файла {file_path}: {e}")