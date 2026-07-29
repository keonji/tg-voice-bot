"""
Сервис транскрибации аудио сообщений.
"""

import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from utils.file_utils import safe_create_temp_file, safe_remove_file
from security import validate_text_input

logger = logging.getLogger(__name__)


class TranscriptionService:
    """Сервис для транскрибации аудио."""

    # Подсказка задаёт стиль вывода (с пунктуацией, с прописными буквами).
    # faster-whisper использует initial_prompt как «затравку» — модель копирует стиль.
    DEFAULT_INITIAL_PROMPT = (
        "Привет, как дела? Сегодня хорошая погода. "
        "Я думаю, что нам нужно обсудить план работы. "
        "Хорошо, договорились! Спасибо за помощь."
    )

    def __init__(self):
        """Инициализация сервиса транскрибации."""
        self.transcription_model = None
        self._model_initialized = False
        self._punct_model = None
        self._punct_model_attempted = False

        # Настройки из окружения
        self.whisper_model = os.getenv("WHISPER_MODEL", "large-v3")
        self.use_faster_whisper = os.getenv("USE_FASTER_WHISPER", "true").lower() == "true"
        self.whisper_device = os.getenv("WHISPER_DEVICE", "cpu")
        self.transcription_language = os.getenv("TRANSCRIPTION_LANGUAGE", "ru")
        self.add_punctuation = os.getenv("ADD_PUNCTUATION", "true").lower() == "true"
        self.initial_prompt = os.getenv("WHISPER_INITIAL_PROMPT", self.DEFAULT_INITIAL_PROMPT)
        self.beam_size = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
        # int8 на CPU — заметно быстрее float32 при незначительной потере качества.
        # Варианты: int8, int8_float32, float32 (CPU); float16, int8_float16 (CUDA).
        default_compute = "int8" if self.whisper_device == "cpu" else "float16"
        self.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", default_compute)

        # Директории
        self.models_dir = Path(os.getenv("MODELS_DIR", "./models"))
        self.temp_dir = Path(os.getenv("AUDIO_TEMP_DIR", "./temp_audio"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # Известные CTranslate2-репозитории для размеров, которых нет у Systran.
    # faster-whisper распознаёт короткие имена только для официальных сборок Systran;
    # turbo туда не входит, поэтому подставляем конкретный repo-id.
    WHISPER_REPO_OVERRIDES = {
        "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    }

    def _resolve_whisper_model(self, whisper_dir: Path) -> str:
        """
        Определяет, что передать в WhisperModel: локальный путь, repo-id или
        короткое имя размера.

        Приоритет:
        1. WHISPER_MODEL — существующий путь на диске (готовая CTranslate2-папка)
        2. WHISPER_MODEL — полный repo-id вида "org/name"
        3. Известный override для размеров без официальной сборки Systran (turbo)
        4. Короткое имя размера — faster-whisper сам возьмёт сборку Systran
        """
        name = self.whisper_model

        local_path = Path(name)
        if local_path.is_dir() and (local_path / "model.bin").exists():
            logger.info(f"Whisper: используется локальная модель {local_path}")
            return str(local_path)

        if "/" in name:
            logger.info(f"Whisper: используется repo-id {name}")
            return name

        override = self.WHISPER_REPO_OVERRIDES.get(name)
        if override:
            logger.info(f"Whisper: для '{name}' используется {override}")
            return override

        return name

    def is_model_loaded(self) -> bool:
        """Загружена ли модель в память (инициализация ленивая)."""
        return self.transcription_model is not None

    def _init_transcription_model(self):
        """Ленивая инициализация модели транскрибации."""
        if self._model_initialized:
            return self.transcription_model

        try:
            if self.use_faster_whisper:
                from faster_whisper import WhisperModel

                whisper_dir = self.models_dir / "whisper"
                whisper_dir.mkdir(parents=True, exist_ok=True)
                model_name = self._resolve_whisper_model(whisper_dir)

                self.transcription_model = WhisperModel(
                    model_name,
                    device=self.whisper_device,
                    compute_type=self.compute_type,
                    download_root=str(whisper_dir),
                    local_files_only=False,
                )
                logger.info(
                    f"Инициализирована faster-whisper модель: {model_name} "
                    f"({self.whisper_device}, {self.compute_type})"
                )

            else:
                import whisper

                whisper_dir = self.models_dir / "whisper"
                if whisper_dir.exists():
                    os.environ['WHISPER_CACHE_DIR'] = str(whisper_dir)

                self.transcription_model = whisper.load_model(
                    self.whisper_model,
                    device=self.whisper_device,
                    download_root=str(whisper_dir) if whisper_dir.exists() else None
                )
                logger.info(f"Инициализирована OpenAI Whisper модель: {self.whisper_model}")

            self._model_initialized = True
            return self.transcription_model

        except Exception as e:
            logger.error(f"Ошибка инициализации Whisper модели: {e}")
            self._model_initialized = True
            return None

    def _init_punct_model(self):
        """Ленивая инициализация модели восстановления пунктуации (silero)."""
        if self._punct_model_attempted:
            return self._punct_model
        self._punct_model_attempted = True
        try:
            # Храним кэш моделей torch.hub рядом с проектными моделями
            torch_hub_dir = self.models_dir / "torch_hub"
            torch_hub_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault('TORCH_HOME', str(torch_hub_dir))
            import torch
            torch.hub.set_dir(str(torch_hub_dir / "hub"))
            torch.set_num_threads(int(os.getenv("PUNCTUATION_THREADS", "4")))
            # silero_te: восстановление пунктуации и регистра для ru/en/de/es
            self._punct_model, _, _, _, apply_te = torch.hub.load(
                repo_or_dir='snakers4/silero-models',
                model='silero_te',
                trust_repo=True,
            )
            self._punct_model_apply = apply_te
            logger.info("Инициализирована silero_te модель восстановления пунктуации")
        except Exception as e:
            logger.warning(f"Не удалось инициализировать silero_te: {e}. Используется простой постпроцессинг.")
            self._punct_model = None
        return self._punct_model

    async def transcribe_telegram_audio(self, bot, audio_file) -> Optional[Dict[str, Any]]:
        """Транскрибирует аудио файл из Telegram."""
        temp_file_path = None

        try:
            # Проверка размера файла (Telegram bot API ограничен 20MB на getFile)
            file_size = getattr(audio_file, 'file_size', None)
            if file_size and file_size > 20 * 1024 * 1024:
                logger.warning(f"Аудио файл слишком большой: {file_size} байт (>20MB)")
                return {
                    'text': "Аудио файл слишком большой для скачивания (>20 МБ). Запишите более короткое сообщение или разбейте на части.",
                    'language': None,
                    'confidence': 0.0,
                    'too_large': True,
                }

            temp_file_path = safe_create_temp_file(
                suffix='.ogg',
                prefix='telegram_audio_',
                dir_path=self.temp_dir
            )

            file_obj = await bot.get_file(audio_file.file_id)
            await file_obj.download_to_drive(temp_file_path)

            logger.info(f"Скачан аудио файл: {temp_file_path} ({file_size or '?'} байт)")

            result = await self._transcribe_audio_file(temp_file_path)

            return result

        except Exception as e:
            logger.error(f"Ошибка транскрибации Telegram аудио: {e}")
            return None
        finally:
            if temp_file_path:
                safe_remove_file(temp_file_path)

    async def _transcribe_audio_file(self, audio_path: Path) -> Optional[Dict[str, Any]]:
        """Транскрибирует аудио файл."""
        transcription_model = self._init_transcription_model()

        if not transcription_model:
            logger.error("Модель транскрибации недоступна")
            return {
                'text': "Ошибка: модель транскрибации недоступна",
                'language': None,
                'confidence': 0.0
            }

        try:
            # Запускаем CPU-bound транскрибацию в отдельном потоке, чтобы не блокировать
            # event loop. Для длинных голосовых это критично — иначе бот «зависает».
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._run_transcribe_sync, audio_path)

            if not result:
                return {
                    'text': "Не удалось распознать аудио",
                    'language': None,
                    'confidence': 0.0,
                    'punctuation_added': False
                }

            # Добавляем/восстанавливаем пунктуацию
            if self.add_punctuation and result.get('text'):
                try:
                    enhanced_text = await loop.run_in_executor(None, self._restore_punctuation, result['text'])
                    result['text'] = enhanced_text
                    result['punctuation_added'] = True
                except Exception as e:
                    logger.warning(f"Ошибка восстановления пунктуации: {e}")
                    result['punctuation_added'] = False
            else:
                result['punctuation_added'] = False

            # Валидируем результат
            if result['text']:
                try:
                    result['text'] = validate_text_input(result['text'], max_length=20000)
                except ValueError as e:
                    logger.warning(f"Некорректная транскрибация: {e}")
                    result['text'] = "Ошибка: транскрибированный текст содержит недопустимые символы"

            return result if result['text'] else {
                'text': "Не удалось распознать аудио",
                'language': None,
                'confidence': 0.0,
                'punctuation_added': False
            }

        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}", exc_info=True)
            return {
                'text': "Ошибка распознавания аудио",
                'language': None,
                'confidence': 0.0,
                'punctuation_added': False
            }

    def _run_transcribe_sync(self, audio_path: Path) -> Optional[Dict[str, Any]]:
        """Синхронная часть транскрибации, запускается в executor."""
        if self.use_faster_whisper:
            segments, info = self.transcription_model.transcribe(
                str(audio_path),
                language=self.transcription_language,
                beam_size=self.beam_size,
                best_of=self.beam_size,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                # initial_prompt задаёт стиль (с пунктуацией) — это сильно повышает шанс,
                # что модель вернёт текст с запятыми/точками вместо «потока сознания».
                initial_prompt=self.initial_prompt,
                # Включаем условие на предыдущий текст: модель смотрит на стиль
                # предыдущих чанков (пунктуация переносится).
                condition_on_previous_text=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=700),
                # word_timestamps убираем: на длинных аудио сильно замедляет и съедает ОЗУ,
                # а нам они не нужны.
                word_timestamps=False,
            )

            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            raw_text = " ".join(text_parts).strip()

            logger.info(
                f"Faster-Whisper транскрибация завершена. Язык: {info.language}, "
                f"уверенность: {info.language_probability:.2f}, длина: {len(raw_text)} симв., "
                f"длительность аудио: {info.duration:.1f}с"
            )

            return {
                'text': raw_text,
                'language': info.language,
                'confidence': info.language_probability,
            }
        else:
            result_dict = self.transcription_model.transcribe(
                str(audio_path),
                language=self.transcription_language,
                temperature=0.0,
                beam_size=self.beam_size,
                best_of=self.beam_size,
                initial_prompt=self.initial_prompt,
                condition_on_previous_text=True,
                fp16=False if self.whisper_device == "cpu" else True
            )

            raw_text = result_dict["text"].strip()
            logger.info(f"OpenAI Whisper транскрибация завершена. Язык: {result_dict.get('language', 'unknown')}")

            return {
                'text': raw_text,
                'language': result_dict.get('language', 'unknown'),
                'confidence': 1.0,
            }

    def _restore_punctuation(self, text: str) -> str:
        """
        Восстанавливает пунктуацию в тексте.
        1) Если текст уже содержит достаточно пунктуации — оставляем как есть.
        2) Иначе пробуем silero_te (если доступна).
        3) В крайнем случае — простой постпроцессинг (точка в конце, заглавная в начале).
        """
        if not text:
            return text

        text = text.strip()

        # Если пунктуация уже есть в разумном количестве — ничего не делаем.
        if self._has_enough_punctuation(text):
            return self._light_postprocess(text)

        # Пробуем silero_te
        model = self._init_punct_model()
        if model is not None:
            try:
                # apply_te ожидает строку и lang ('ru', 'en', ...).
                lang = self.transcription_language if self.transcription_language in ('ru', 'en', 'de', 'es') else 'ru'
                restored = self._punct_model_apply(text.lower(), lan=lang)
                if restored and self._has_enough_punctuation(restored):
                    return restored.strip()
            except Exception as e:
                logger.warning(f"silero_te не удался: {e}")

        # Fallback: лёгкая обработка
        return self._light_postprocess(text)

    @staticmethod
    def _has_enough_punctuation(text: str) -> bool:
        """Считает, что пунктуации «достаточно», если на каждые ~15 слов есть знак препинания."""
        words = text.split()
        if len(words) < 6:
            return True  # короткие фразы — не требуют дробления
        punct_count = sum(1 for ch in text if ch in '.,!?;:')
        return punct_count >= max(1, len(words) // 15)

    @staticmethod
    def _light_postprocess(text: str) -> str:
        """Простая косметика: первая буква заглавная, точка в конце."""
        text = text.strip()
        if not text:
            return text
        text = text[0].upper() + text[1:] if text[0].isalpha() else text
        if text[-1] not in '.!?…':
            text += '.'
        # Двойные пробелы → одинарные
        text = re.sub(r'\s+', ' ', text)
        return text


# Глобальный экземпляр сервиса
_transcription_service: Optional[TranscriptionService] = None


def get_transcription_service() -> TranscriptionService:
    """Возвращает глобальный экземпляр сервиса транскрибации."""
    global _transcription_service
    if _transcription_service is None:
        _transcription_service = TranscriptionService()
    return _transcription_service
