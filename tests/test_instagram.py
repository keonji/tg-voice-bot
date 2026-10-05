"""
Тесты пересылки медиа из Instagram.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers import instagram as ig_handlers
from handlers.instagram import build_caption, chunk_media, _send_media
from services.chat_settings import ChatSettings
from services.instagram_service import (
    InstagramDownloadError, InstagramService, MediaCache, MediaItem, extract_instagram_urls, parse_entries,
    parse_instafix_page,
)
from services.media_tools import (
    parse_ffprobe, short_side_for_kbps, target_video_kbps,
)


class TestExtractUrls:
    def test_post_reel_and_tv(self):
        text = (
            "смотри https://www.instagram.com/p/AbC_1-2/?igsh=xyz и "
            "instagram.com/reel/REEL123/ а ещё https://m.instagram.com/tv/TV9/"
        )
        assert extract_instagram_urls(text) == [
            "https://www.instagram.com/p/AbC_1-2/",
            "https://www.instagram.com/p/REEL123/",
            "https://www.instagram.com/p/TV9/",
        ]

    def test_username_prefixed_and_reels(self):
        assert extract_instagram_urls("https://instagram.com/someone/reels/XyZ/") == [
            "https://www.instagram.com/p/XyZ/"
        ]

    def test_duplicates_removed(self):
        text = "https://instagram.com/p/A1/ https://www.instagram.com/reel/A1/"
        assert extract_instagram_urls(text) == ["https://www.instagram.com/p/A1/"]

    def test_profile_and_other_sites_ignored(self):
        text = "https://instagram.com/someone/ https://example.com/p/A1/ https://notinstagram.com/p/A1/"
        assert extract_instagram_urls(text) == []

    def test_empty(self):
        assert extract_instagram_urls(None) == []


class TestParseFfprobe:
    def test_video_with_audio(self):
        out = json.dumps({
            "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 720, "height": 1280},
                        {"codec_type": "audio"}],
            "format": {"duration": "12.6"},
        })
        info = parse_ffprobe(out)
        assert info.has_audio
        assert (info.vcodec, info.width, info.height, info.duration) == ("vp9", 720, 1280, 12.6)

    def test_video_without_audio(self):
        out = json.dumps({"streams": [{"codec_type": "video", "width": 1, "height": 1}], "format": {}})
        info = parse_ffprobe(out)
        assert not info.has_audio
        assert info.duration is None

    def test_garbage_output_assumes_audio(self):
        assert parse_ffprobe("not json").has_audio


class TestCompressionMath:
    def test_target_bitrate_fits_limit(self):
        limit = 50 * 1024 * 1024
        kbps = target_video_kbps(limit, 120, 96)
        assert (kbps + 96) * 1000 / 8 * 120 <= limit

    def test_resolution_steps(self):
        assert short_side_for_kbps(4000) == 1080
        assert short_side_for_kbps(1500) == 720
        assert short_side_for_kbps(500) == 480


class TestParseEntries:
    def test_single_video(self):
        entries = parse_entries({"id": "V1", "formats": [{"url": "u"}]})
        assert [(e.media_id, e.is_video) for e in entries] == [("V1", True)]

    def test_single_photo_takes_best_thumbnail(self):
        entries = parse_entries({"id": "P1", "formats": [], "thumbnails": [{"url": "small"}, {"url": "orig"}]})
        assert entries[0].url == "orig" and not entries[0].is_video

    def test_carousel_order(self):
        info = {"_type": "playlist", "entries": [
            {"id": "a", "formats": [], "thumbnails": [{"url": "pa"}]},
            {"id": "b", "formats": [{"url": "v"}]},
            {"id": "c", "thumbnails": [{"url": "pc"}]},
        ]}
        assert [(e.media_id, e.is_video) for e in parse_entries(info)] == [
            ("a", False), ("b", True), ("c", False)]


def video(path="v.mp4", audio=True, compressed=False):
    return MediaItem(path=Path(path), is_video=True, has_audio=audio, width=720, height=1280,
                     duration=5, compressed=compressed)


def photo(path="p.jpg", compressed=False):
    return MediaItem(path=Path(path), is_video=False, compressed=compressed)


def failed(is_video=True):
    return MediaItem(path=None, is_video=is_video)


class TestCaption:
    def test_no_warnings(self):
        assert build_caption([video(), photo()]) == ""

    def test_single_silent_video(self):
        assert "Видео без звука" in build_caption([video(audio=False)])

    def test_carousel_silent_numbers(self):
        caption = build_caption([photo(), video(audio=False), video(), video(audio=False)])
        assert "№2, №4" in caption

    def test_single_compressed(self):
        assert "Сжато" in build_caption([video(compressed=True)])

    def test_carousel_compressed_numbers(self):
        assert "Сжаты под лимит Telegram: №1, №3" in build_caption(
            [photo(compressed=True), video(), video(compressed=True)])

    def test_failed_numbers(self):
        assert "Не удалось отправить: №2" in build_caption([photo(), failed()])


class TestChunkMedia:
    @pytest.mark.parametrize("n", range(1, 31))
    def test_no_single_item_albums(self, n):
        chunks = chunk_media(list(range(n)))
        assert sum(chunks, []) == list(range(n))
        assert all(len(c) <= 10 for c in chunks)
        if n > 1:
            assert all(len(c) >= 2 for c in chunks)


class TestChatSettings:
    def test_default_disabled_and_persisted(self, tmp_path):
        path = tmp_path / "s.json"
        settings = ChatSettings(path)
        assert not settings.is_enabled(-100, ChatSettings.INSTAGRAM)

        settings.set_enabled(-100, ChatSettings.INSTAGRAM, True)
        reloaded = ChatSettings(path)
        assert reloaded.is_enabled(-100, ChatSettings.INSTAGRAM)
        assert not reloaded.is_enabled(-200, ChatSettings.INSTAGRAM)

        reloaded.set_enabled(-100, ChatSettings.INSTAGRAM, False)
        assert not ChatSettings(path).is_enabled(-100, ChatSettings.INSTAGRAM)

    def test_corrupted_file(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{oops")
        assert not ChatSettings(path).is_enabled(1, ChatSettings.INSTAGRAM)


def make_files(tmp_path, items):
    for item in items:
        item.path = tmp_path / item.path
        item.path.write_bytes(b"x")
    return items


class TestSendMedia:
    @pytest.mark.asyncio
    async def test_single_silent_video_has_warning(self, tmp_path):
        message = MagicMock(reply_video=AsyncMock(), reply_media_group=AsyncMock())
        await _send_media(message, make_files(tmp_path, [video(audio=False)]))
        kwargs = message.reply_video.await_args.kwargs
        assert "без звука" in kwargs["caption"]
        assert kwargs["quote"] is True
        message.reply_media_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_carousel_as_albums(self, tmp_path):
        message = MagicMock(reply_media_group=AsyncMock())
        items = make_files(tmp_path, [photo(f"{i:02}.jpg") for i in range(11)])
        await _send_media(message, items)
        sizes = [len(c.kwargs["media"]) for c in message.reply_media_group.await_args_list]
        assert sizes == [6, 5]

    @pytest.mark.asyncio
    async def test_nothing_sendable_replies_text(self):
        message = MagicMock(reply_text=AsyncMock(), reply_video=AsyncMock())
        await _send_media(message, [failed()])
        message.reply_video.assert_not_awaited()
        assert "Не удалось подготовить" in message.reply_text.await_args.args[0]

    @pytest.mark.asyncio
    async def test_failed_item_left_out_of_album(self, tmp_path):
        message = MagicMock(reply_media_group=AsyncMock())
        items = make_files(tmp_path, [photo("a.jpg"), photo("b.jpg")]) + [failed()]
        await _send_media(message, items)
        media = message.reply_media_group.await_args.kwargs["media"]
        assert len(media) == 2
        assert "№3" in media[0].caption


def make_update(text, chat_id=-100, chat_type="supergroup"):
    message = MagicMock()
    message.text = text
    message.caption = None
    message.chat_id = chat_id
    message.chat.id = chat_id
    message.chat.type = chat_type
    message.parse_entities.return_value = {}
    message.parse_caption_entities.return_value = {}
    message.reply_text = AsyncMock()
    return MagicMock(message=message)


def make_service(tmp_path, monkeypatch, download, ttl=3600):
    monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("INSTAGRAM_CACHE_TTL", str(ttl))
    service = InstagramService()
    service.download = download
    return service


def downloaded(tmp_path, *names):
    """Фабрика результата download(): свежая рабочая директория с файлами."""
    counter = {"n": 0}

    async def download(url):
        counter["n"] += 1
        work_dir = tmp_path / f"work{counter['n']}"
        work_dir.mkdir()
        items = []
        for name in names:
            (work_dir / name).write_bytes(b"x")
            items.append(video(str(work_dir / name)) if name.endswith(".mp4") else photo(str(work_dir / name)))
        return work_dir, items

    return AsyncMock(side_effect=download)


def make_context():
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


class TestLinkHandler:
    @pytest.fixture
    def settings(self, tmp_path):
        settings = ChatSettings(tmp_path / "s.json")
        settings.set_enabled(-100, ChatSettings.INSTAGRAM, True)
        return settings

    @pytest.mark.asyncio
    async def test_private_chat_enabled_by_default_but_can_be_disabled(self, tmp_path, monkeypatch):
        settings = ChatSettings(tmp_path / "s.json")
        service = make_service(tmp_path, monkeypatch, AsyncMock(side_effect=InstagramDownloadError("x")))
        with patch.object(ig_handlers, "get_chat_settings", return_value=settings), \
                patch.object(ig_handlers, "get_instagram_service", return_value=service):
            await ig_handlers.instagram_link_handler(
                make_update("https://instagram.com/p/A1/", chat_id=42, chat_type="private"), make_context())
            assert service.download.await_count == 1

            settings.set_enabled(42, ChatSettings.INSTAGRAM, False)
            await ig_handlers.instagram_link_handler(
                make_update("https://instagram.com/p/A1/", chat_id=42, chat_type="private"), make_context())
            assert service.download.await_count == 1

    @pytest.mark.asyncio
    async def test_disabled_chat_ignored(self, tmp_path, monkeypatch):
        settings = ChatSettings(tmp_path / "s.json")
        service = make_service(tmp_path, monkeypatch, AsyncMock())
        with patch.object(ig_handlers, "get_chat_settings", return_value=settings), \
                patch.object(ig_handlers, "get_instagram_service", return_value=service):
            await ig_handlers.instagram_link_handler(make_update("https://instagram.com/p/A1/"), make_context())
        service.download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repeated_link_served_from_cache(self, tmp_path, monkeypatch, settings):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4", "b.jpg"))
        sent = []

        async def send(message, items):
            sent.append([(i.path, i.path.read_bytes()) for i in items])

        with patch.object(ig_handlers, "get_chat_settings", return_value=settings), \
                patch.object(ig_handlers, "get_instagram_service", return_value=service), \
                patch.object(ig_handlers, "_send_media", side_effect=send):
            for _ in range(2):
                await ig_handlers.instagram_link_handler(make_update("https://instagram.com/reel/A1/"), make_context())

        assert service.download.await_count == 1
        assert sent[0] == sent[1]
        assert not (tmp_path / "work1").exists()

    @pytest.mark.asyncio
    async def test_send_failure_keeps_cache(self, tmp_path, monkeypatch, settings):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4"))
        update = make_update("https://instagram.com/p/A1/")
        with patch.object(ig_handlers, "get_chat_settings", return_value=settings), \
                patch.object(ig_handlers, "get_instagram_service", return_value=service), \
                patch.object(ig_handlers, "_send_media", AsyncMock(side_effect=RuntimeError("upload failed"))):
            await ig_handlers.instagram_link_handler(update, make_context())
        assert "Не удалось загрузить" in update.message.reply_text.await_args.args[0]
        assert service.cache.load("A1") is not None

    @pytest.mark.asyncio
    async def test_download_error_replies(self, tmp_path, monkeypatch, settings):
        service = make_service(tmp_path, monkeypatch, AsyncMock(side_effect=InstagramDownloadError("429")))
        update = make_update("https://instagram.com/p/A1/")
        with patch.object(ig_handlers, "get_chat_settings", return_value=settings), \
                patch.object(ig_handlers, "get_instagram_service", return_value=service):
            await ig_handlers.instagram_link_handler(update, make_context())
        assert "Не удалось скачать" in update.message.reply_text.await_args.args[0]


class TestMediaCache:
    URL = "https://www.instagram.com/p/A1/"

    @pytest.mark.asyncio
    async def test_expired_cache_purged_and_redownloaded(self, tmp_path, monkeypatch):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4"))
        async with service.fetch(self.URL):
            pass
        post_dir = service.cache.root / "A1"
        assert post_dir.exists() and service.purge() == 0

        old = time.time() - 3601
        os.utime(post_dir / MediaCache.MANIFEST, (old, old))
        assert service.purge() == 1 and not post_dir.exists()

        async with service.fetch(self.URL):
            pass
        assert service.download.await_count == 2

    @pytest.mark.asyncio
    async def test_in_use_not_purged(self, tmp_path, monkeypatch):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4"))
        async with service.fetch(self.URL) as items:
            old = time.time() - 3601
            os.utime(service.cache.root / "A1" / MediaCache.MANIFEST, (old, old))
            assert service.purge() == 0
            assert items[0].path.exists()
        assert service.purge() == 1

    @pytest.mark.asyncio
    async def test_concurrent_same_link_downloads_once(self, tmp_path, monkeypatch):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4"))

        async def use():
            async with service.fetch(self.URL) as items:
                return items[0].path

        paths = await asyncio.gather(use(), use(), use())
        assert service.download.await_count == 1
        assert len(set(paths)) == 1

    @pytest.mark.asyncio
    async def test_partial_failure_not_cached(self, tmp_path, monkeypatch):
        async def download(url):
            work_dir = tmp_path / "w"
            work_dir.mkdir(exist_ok=True)
            (work_dir / "a.jpg").write_bytes(b"x")
            return work_dir, [photo(str(work_dir / "a.jpg")), failed()]

        service = make_service(tmp_path, monkeypatch, AsyncMock(side_effect=download))
        async with service.fetch(self.URL) as items:
            assert items[0].path.exists()
        assert not (tmp_path / "w").exists()
        assert service.cache.load("A1") is None

    @pytest.mark.asyncio
    async def test_ttl_zero_deletes_after_send(self, tmp_path, monkeypatch):
        service = make_service(tmp_path, monkeypatch, downloaded(tmp_path, "a.mp4"), ttl=0)
        async with service.fetch(self.URL) as items:
            path = items[0].path
            assert path.exists()
        assert not path.exists()
        assert not (service.cache.root / "A1").exists()

    @pytest.mark.asyncio
    async def test_metadata_survives_roundtrip(self, tmp_path, monkeypatch):
        async def download(url):
            work_dir = tmp_path / "w"
            work_dir.mkdir()
            (work_dir / "a.mp4").write_bytes(b"x")
            return work_dir, [MediaItem(path=work_dir / "a.mp4", is_video=True, has_audio=False,
                                        width=1, height=2, duration=3, compressed=True)]

        service = make_service(tmp_path, monkeypatch, AsyncMock(side_effect=download))
        async with service.fetch(self.URL):
            pass
        item = service.cache.load("A1")[0]
        assert (item.has_audio, item.width, item.height, item.duration, item.compressed) == (False, 1, 2, 3, True)

    def test_stale_work_dirs_purged(self, tmp_path, monkeypatch):
        service = make_service(tmp_path, monkeypatch, AsyncMock())
        stale = service.temp_root / "ig_old"
        fresh = service.temp_root / "ig_new"
        stale.mkdir(parents=True)
        fresh.mkdir()
        old = time.time() - 7200
        os.utime(stale, (old, old))
        assert service.purge() == 1
        assert not stale.exists() and fresh.exists()


class TestInstafixPage:
    BASE = "https://instagramfix.com/"

    def test_video(self):
        html = '<meta property="og:video" content="/videos/X/1"/><meta property="og:image" content="/i"/>'
        entry = parse_instafix_page(html, self.BASE)
        assert entry.is_video and entry.url == "https://instagramfix.com/videos/X/1"

    def test_image(self):
        html = '<meta name="twitter:image" content="/images/X/2"/><meta property="og:image" content="/images/X/2"/>'
        entry = parse_instafix_page(html, self.BASE)
        assert not entry.is_video and entry.url == "https://instagramfix.com/images/X/2"

    def test_absolute_url_kept(self):
        html = '<meta property="og:video" content="https://cdn.example/v.mp4"/>'
        assert parse_instafix_page(html, self.BASE).url == "https://cdn.example/v.mp4"

    def test_out_of_range(self):
        html = '<meta property="og:description" content="Media number out of range"/>'
        assert parse_instafix_page(html, self.BASE) is None

    def test_no_media(self):
        assert parse_instafix_page("<html></html>", self.BASE) is None


class TestServiceDownload:
    @pytest.mark.asyncio
    async def test_falls_back_to_instafix(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path))
        service = InstagramService()
        fallback = AsyncMock(return_value=[])
        with patch.object(service, "_run_ytdlp", AsyncMock(side_effect=InstagramDownloadError("x"))), \
                patch.object(service, "_fetch_instafix", fallback):
            with pytest.raises(InstagramDownloadError, match="не найдено медиа"):
                await service.download("https://www.instagram.com/p/A1/")
        fallback.assert_awaited_once_with("A1")

    @pytest.mark.asyncio
    async def test_failed_download_removes_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path))
        monkeypatch.setenv("INSTAFIX_HOSTS", "")
        service = InstagramService()
        with patch.object(service, "_run_ytdlp", AsyncMock(side_effect=InstagramDownloadError("x"))):
            with pytest.raises(InstagramDownloadError):
                await service.download("https://www.instagram.com/p/A1/")
        assert list((tmp_path / "instagram").iterdir()) == []

    @pytest.mark.asyncio
    async def test_anonymous_retries_then_cookies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path))
        monkeypatch.setenv("INSTAGRAM_COOKIES", "/c.txt")
        service = InstagramService()
        run = AsyncMock(side_effect=[InstagramDownloadError("empty"), InstagramDownloadError("empty"), b"{}"])
        with patch.object(service, "_run_ytdlp", run):
            await service._fetch_info("https://www.instagram.com/p/A1/", tmp_path)
        calls = [c.args[0] for c in run.await_args_list]
        assert ["--cookies" in args for args in calls] == [False, False, True]

    @pytest.mark.asyncio
    async def test_no_cookies_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path))
        monkeypatch.delenv("INSTAGRAM_COOKIES", raising=False)
        service = InstagramService()
        run = AsyncMock(side_effect=InstagramDownloadError("login required"))
        with patch.object(service, "_run_ytdlp", run):
            with pytest.raises(InstagramDownloadError, match="login required"):
                await service._fetch_info("https://www.instagram.com/p/A1/", tmp_path)
        assert run.await_count == 2
