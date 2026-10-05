"""
Скачивание постов Instagram (видео, фото, карусели): yt-dlp, запасной путь — InstaFix.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from services.media_tools import MediaTools, VideoInfo, run

logger = logging.getLogger(__name__)

INSTAGRAM_URL_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.|m\.)?instagram\.com/"
    r"(?:[A-Za-z0-9_.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

# Лимиты Bot API на загрузку файлов
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
}

# Прогрессивный mp4 (H.264 + звук); если его нет — склейка DASH, затем видео без звука
VIDEO_FORMAT = "b/bv*+ba/bv*"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Referer": "https://www.instagram.com/",
}

# InstaFix отдаёт OG-теги с медиа только ботам-превьюерам, браузер редиректит в Instagram
INSTAFIX_HEADERS = {"User-Agent": "TelegramBot (like TwitterBot)"}
INSTAFIX_OUT_OF_RANGE = "Media number out of range"
MAX_CAROUSEL_ITEMS = 20
STALE_WORK_DIR_SECONDS = 3600
META_RE = re.compile(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', re.IGNORECASE)


def extract_instagram_urls(text: str) -> List[str]:
    """Канонические ссылки на посты Instagram из текста, без дублей, в порядке появления."""
    urls = []
    for shortcode in INSTAGRAM_URL_RE.findall(text or ""):
        url = f"https://www.instagram.com/p/{shortcode}/"
        if url not in urls:
            urls.append(url)
    return urls


def shortcode_from_url(url: str) -> str:
    match = INSTAGRAM_URL_RE.search(url)
    if not match:
        raise InstagramDownloadError(f"не ссылка на пост: {url}")
    return match.group(1)


class InstagramDownloadError(Exception):
    """Пост не удалось скачать."""


@dataclass
class MediaItem:
    """Элемент поста; path=None — не удалось подготовить под лимиты Telegram."""
    path: Optional[Path]
    is_video: bool
    has_audio: bool = True
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    compressed: bool = False


class MediaCache:
    """
    Готовые к отправке медиа постов на диске: <root>/<shortcode>/ + manifest.json.
    Время жизни отсчитывается от скачивания; повтор ссылки в течение TTL не качает пост заново.
    """

    MANIFEST = "manifest.json"

    def __init__(self, root: Path, ttl: int):
        self.root = root
        self.ttl = ttl
        self._in_use: Dict[str, int] = defaultdict(int)

    def _dir(self, shortcode: str) -> Path:
        return self.root / shortcode

    def _expired(self, post_dir: Path, now: float) -> bool:
        try:
            return now - (post_dir / self.MANIFEST).stat().st_mtime >= self.ttl
        except FileNotFoundError:
            return True

    def load(self, shortcode: str) -> Optional[List[MediaItem]]:
        post_dir = self._dir(shortcode)
        if self.ttl <= 0 or self._expired(post_dir, time.time()):
            return None
        try:
            records = json.loads((post_dir / self.MANIFEST).read_text(encoding="utf-8"))
            items = [MediaItem(**{**r, "path": post_dir / r["path"]}) for r in records]
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.warning(f"Кэш {shortcode} повреждён: {e}")
            return None
        if not all(item.path.exists() for item in items):
            return None
        return items

    def store(self, shortcode: str, items: List[MediaItem]) -> Optional[List[MediaItem]]:
        """Переносит файлы в кэш. Посты с неподготовленными элементами не кэшируются — сбой мог быть случайным."""
        if self.ttl <= 0 or not items or not all(item.path for item in items):
            return None
        # Просроченная копия ещё отправляется — не трогаем её, этот раз обходимся без кэша
        if self._in_use.get(shortcode):
            return None
        post_dir = self._dir(shortcode)
        shutil.rmtree(post_dir, ignore_errors=True)
        post_dir.mkdir(parents=True)
        cached, records = [], []
        for n, item in enumerate(items, 1):
            target = post_dir / f"{n:03d}{item.path.suffix.lower()}"
            shutil.move(str(item.path), target)
            cached.append(MediaItem(**{**asdict(item), "path": target}))
            records.append({**asdict(item), "path": target.name})
        # manifest пишется последним: его mtime — момент готовности кэша
        (post_dir / self.MANIFEST).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return cached

    @asynccontextmanager
    async def using(self, shortcode: str) -> AsyncIterator[None]:
        """Пока пост отправляется, purge его не трогает, даже если TTL истёк."""
        self._in_use[shortcode] += 1
        try:
            yield
        finally:
            self._in_use[shortcode] -= 1
            if not self._in_use[shortcode]:
                del self._in_use[shortcode]

    def purge(self) -> int:
        if not self.root.exists():
            return 0
        now, removed = time.time(), 0
        for post_dir in self.root.iterdir():
            if not post_dir.is_dir() or self._in_use.get(post_dir.name):
                continue
            if self._expired(post_dir, now):
                shutil.rmtree(post_dir, ignore_errors=True)
                removed += 1
        return removed


@dataclass
class _Entry:
    media_id: str
    is_video: bool
    file: Optional[Path] = None
    url: Optional[str] = None


def parse_entries(info: dict) -> List[_Entry]:
    """Элементы поста из JSON yt-dlp в порядке карусели."""
    entries = []
    for entry in info.get("entries") or [info]:
        if not entry:
            continue
        media_id = str(entry.get("id"))
        if entry.get("formats"):
            entries.append(_Entry(media_id, True))
        else:
            thumbnails = [t for t in entry.get("thumbnails") or [] if t.get("url")]
            if thumbnails:
                # yt-dlp сортирует миниатюры по возрастанию качества: последняя — оригинал
                entries.append(_Entry(media_id, False, url=thumbnails[-1]["url"]))
    return entries


def parse_instafix_page(html: str, base_url: str) -> Optional[_Entry]:
    """Элемент поста по OG-тегам страницы InstaFix; None — элементов больше нет."""
    meta = {}
    for key, value in META_RE.findall(html):
        meta.setdefault(key.lower(), value)
    if INSTAFIX_OUT_OF_RANGE in meta.get("og:description", ""):
        return None
    video = meta.get("og:video") or meta.get("twitter:player:stream")
    image = meta.get("og:image") or meta.get("twitter:image")
    if not video and not image:
        return None
    return _Entry("", is_video=bool(video), url=urljoin(base_url, video or image))


class InstagramService:
    """Скачивает медиа поста во временную директорию и готовит их к отправке."""

    def __init__(self):
        self.temp_root = Path(os.getenv("AUDIO_TEMP_DIR", "./temp_audio")) / "instagram"
        self.cookies_file = os.getenv("INSTAGRAM_COOKIES", "").strip() or None
        self.download_timeout = int(os.getenv("INSTAGRAM_DOWNLOAD_TIMEOUT", "180"))
        self.anonymous_attempts = int(os.getenv("INSTAGRAM_ANONYMOUS_ATTEMPTS", "2"))
        self.instafix_hosts = [h.strip() for h in os.getenv("INSTAFIX_HOSTS", "instagramfix.com").split(",")
                               if h.strip()]
        self._semaphore = asyncio.Semaphore(int(os.getenv("INSTAGRAM_MAX_PARALLEL", "2")))
        self._post_locks: Dict[str, asyncio.Lock] = {}
        self.cache = MediaCache(self.temp_root / "cache", int(os.getenv("INSTAGRAM_CACHE_TTL", "3600")))
        self.media_tools = MediaTools()

    @asynccontextmanager
    async def fetch(self, url: str) -> AsyncIterator[List[MediaItem]]:
        """
        Медиа поста: из кэша или со скачиванием. Файлы валидны только внутри контекста;
        после выхода некэшированные удаляются, кэшированные живут до истечения TTL.
        """
        shortcode = shortcode_from_url(url)
        lock = self._post_locks.setdefault(shortcode, asyncio.Lock())
        work_dir = None
        # Лок на пост: вторая такая же ссылка ждёт первую и берёт результат из кэша
        async with lock:
            items = self.cache.load(shortcode)
            if items is not None:
                logger.info(f"Instagram: {shortcode} из кэша")
            else:
                work_dir, items = await self.download(url)
                cached = self.cache.store(shortcode, items)
                if cached is not None:
                    self.cleanup(work_dir)
                    work_dir, items = None, cached
        try:
            async with self.cache.using(shortcode):
                yield items
        finally:
            if work_dir:
                self.cleanup(work_dir)

    def purge(self) -> int:
        """Удаляет просроченный кэш и брошенные рабочие директории (после падения процесса)."""
        removed = self.cache.purge()
        if self.temp_root.exists():
            now = time.time()
            for work_dir in self.temp_root.glob("ig_*"):
                # Скачивание с перекодированием ограничено таймаутами, час заведомо больше
                if work_dir.is_dir() and now - work_dir.stat().st_mtime > STALE_WORK_DIR_SECONDS:
                    self.cleanup(work_dir)
                    removed += 1
        return removed

    async def download(self, url: str) -> tuple[Path, List[MediaItem]]:
        """
        Скачивает пост. Возвращает директорию и медиа в порядке карусели.
        При исключении директория удаляется здесь, иначе — вызывающим через cleanup().
        """
        self.temp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="ig_", dir=self.temp_root))
        try:
            async with self._semaphore:
                try:
                    info_file = await self._fetch_info(url, work_dir)
                    entries = parse_entries(json.loads(info_file.read_text(encoding="utf-8")))
                    if any(e.is_video for e in entries):
                        await self._download_videos(info_file, entries, work_dir)
                    headers = BROWSER_HEADERS
                except InstagramDownloadError as e:
                    if not self.instafix_hosts:
                        raise
                    logger.info(f"yt-dlp не справился ({e}), пробуем InstaFix")
                    entries = await self._fetch_instafix(shortcode_from_url(url))
                    headers = INSTAFIX_HEADERS
                await self._download_urls(entries, work_dir, headers)
            if not entries:
                raise InstagramDownloadError("в посте не найдено медиа")
            items = [await self._prepare(entry) for entry in entries]
            return work_dir, items
        except BaseException:
            self.cleanup(work_dir)
            raise

    @staticmethod
    def cleanup(work_dir: Path):
        shutil.rmtree(work_dir, ignore_errors=True)

    async def _fetch_info(self, url: str, work_dir: Path) -> Path:
        """Анонимно (Instagram иногда отказывает — повторяем), затем с cookies, если заданы."""
        attempts = [None] * self.anonymous_attempts
        if self.cookies_file:
            attempts.append(self.cookies_file)

        last_error = None
        for cookies in attempts:
            try:
                stdout = await self._run_ytdlp(
                    ["--dump-single-json", "--ignore-no-formats-error", *self._cookie_args(cookies), url]
                )
                json.loads(stdout)
            except (InstagramDownloadError, ValueError) as e:
                logger.info(f"yt-dlp {'с cookies' if cookies else 'анонимно'}: {e}")
                last_error = e
                continue
            info_file = work_dir / "info.json"
            info_file.write_bytes(stdout)
            return info_file
        raise InstagramDownloadError(str(last_error))

    @staticmethod
    def _cookie_args(cookies: Optional[str]) -> List[str]:
        return ["--cookies", cookies] if cookies else []

    async def _download_videos(self, info_file: Path, entries: List[_Entry], work_dir: Path):
        """Качает видео по сохранённым метаданным: повторного запроса к Instagram нет, только CDN."""
        try:
            await self._run_ytdlp([
                "--load-info-json", str(info_file), "--ignore-errors", "--ignore-no-formats-error",
                "--format", VIDEO_FORMAT, "--merge-output-format", "mp4",
                "--output", str(work_dir / "%(id)s.%(ext)s"),
            ])
        except InstagramDownloadError as e:
            # Часть видео могла скачаться — недостающие уйдут в подпись как неотправленные
            logger.warning(f"yt-dlp: ошибка скачивания видео: {e}")
        for entry in entries:
            if entry.is_video:
                entry.file = next((p for p in work_dir.glob(f"{entry.media_id}.*")
                                   if p.suffix.lower() in VIDEO_EXTENSIONS), None)

    async def _run_ytdlp(self, args: List[str]) -> bytes:
        cmd = [sys.executable, "-m", "yt_dlp", "--no-progress", "--socket-timeout", "30", *args]
        try:
            code, stdout, stderr = await run(cmd, timeout=self.download_timeout)
        except asyncio.TimeoutError:
            raise InstagramDownloadError(f"таймаут скачивания ({self.download_timeout} с)")

        if code != 0:
            error = next((line for line in stderr.decode(errors="replace").splitlines()
                          if line.startswith("ERROR")), f"код {code}")
            raise InstagramDownloadError(error[:300])
        return stdout

    async def _fetch_instafix(self, shortcode: str) -> List[_Entry]:
        """Перебирает элементы поста через InstaFix: /p/{code}/{n}/ до «out of range»."""
        last_error = None
        for host in self.instafix_hosts:
            base = f"https://{host}/"
            entries = []
            try:
                async with httpx.AsyncClient(headers=INSTAFIX_HEADERS, timeout=30) as client:
                    for n in range(1, MAX_CAROUSEL_ITEMS + 1):
                        response = await client.get(f"{base}p/{shortcode}/{n}/")
                        entry = parse_instafix_page(response.text, base)
                        if not entry:
                            break
                        entry.media_id = f"{shortcode}_{n}"
                        entries.append(entry)
            except httpx.HTTPError as e:
                last_error = e
                logger.info(f"InstaFix {host}: {e!r}")
                continue
            if entries:
                logger.info(f"InstaFix {host}: {len(entries)} элементов")
                return entries
            logger.info(f"InstaFix {host}: медиа не найдено")
        raise InstagramDownloadError(f"InstaFix не помог: {last_error or 'медиа не найдено'}")

    async def _download_urls(self, entries: List[_Entry], work_dir: Path, headers: dict):
        """Качает элементы с прямыми ссылками (фото из yt-dlp, всё из InstaFix)."""
        pending = [e for e in entries if e.url and not e.file]
        if not pending:
            return
        async with httpx.AsyncClient(headers=headers, timeout=120, follow_redirects=True) as client:
            for entry in pending:
                try:
                    response = await client.get(entry.url)
                except httpx.HTTPError as e:
                    logger.warning(f"Медиа {entry.media_id}: {e!r}")
                    continue
                if response.status_code != 200:
                    logger.warning(f"Медиа {entry.media_id}: HTTP {response.status_code}")
                    continue
                content_type = response.headers.get("content-type", "").split(";")[0]
                ext = CONTENT_TYPE_EXTENSIONS.get(content_type, ".mp4" if entry.is_video else ".jpg")
                entry.file = work_dir / f"{entry.media_id}{ext}"
                entry.file.write_bytes(response.content)

    async def _prepare(self, entry: _Entry) -> MediaItem:
        if not entry.file or not entry.file.exists():
            return MediaItem(path=None, is_video=entry.is_video)

        if not entry.is_video:
            path, compressed = await self.media_tools.prepare_photo(entry.file, MAX_PHOTO_BYTES)
            return MediaItem(path=path, is_video=False, compressed=compressed)

        path, info, compressed = await self.media_tools.prepare_video(entry.file, MAX_VIDEO_BYTES)
        return video_item(path, info, compressed)


def video_item(path: Optional[Path], info: VideoInfo, compressed: bool) -> MediaItem:
    return MediaItem(
        path=path, is_video=True, has_audio=info.has_audio,
        width=info.width, height=info.height,
        duration=round(info.duration) if info.duration else None,
        compressed=compressed,
    )


_instagram_service: Optional[InstagramService] = None


def get_instagram_service() -> InstagramService:
    """Возвращает глобальный экземпляр сервиса Instagram."""
    global _instagram_service
    if _instagram_service is None:
        _instagram_service = InstagramService()
    return _instagram_service
