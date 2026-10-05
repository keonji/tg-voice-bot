"""
Персистентные настройки чатов (включённые функции).
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ChatSettings:
    """Флаги функций по чатам, хранятся в JSON-файле."""

    INSTAGRAM = "instagram"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            logger.error(f"Не удалось прочитать настройки чатов {self.path}: {e}")
            return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def is_enabled(self, chat_id: int, feature: str, default: bool = False) -> bool:
        return bool(self._data.get(str(chat_id), {}).get(feature, default))

    def set_enabled(self, chat_id: int, feature: str, enabled: bool):
        with self._lock:
            self._data.setdefault(str(chat_id), {})[feature] = enabled
            self._save()


_chat_settings: Optional[ChatSettings] = None


def get_chat_settings() -> ChatSettings:
    """Возвращает глобальный экземпляр настроек чатов."""
    global _chat_settings
    if _chat_settings is None:
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        _chat_settings = ChatSettings(data_dir / "chat_settings.json")
    return _chat_settings
