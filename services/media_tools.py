"""
ffprobe/ffmpeg: анализ медиа и приведение к лимитам Telegram.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Telegram-клиенты (особенно iOS) надёжно проигрывают только H.264
PLAYABLE_VIDEO_CODECS = {"h264"}
PHOTO_EXTENSIONS_AS_IS = {".jpg", ".jpeg", ".png"}

MIN_VIDEO_KBPS = 150
SIZE_SAFETY = 0.95

# Перекодирование грузит CPU, на котором крутится Whisper — не больше одного за раз
_transcode_lock = asyncio.Lock()


@dataclass
class VideoInfo:
    has_audio: bool = True
    vcodec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None


async def run(cmd: List[str], timeout: float) -> Tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, stdout, stderr


def parse_ffprobe(output: str) -> VideoInfo:
    """VideoInfo по JSON-выводу ffprobe; при мусоре — пустой (звук считаем присутствующим)."""
    try:
        data = json.loads(output)
    except ValueError:
        return VideoInfo()

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    info = VideoInfo(
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        vcodec=video.get("codec_name"),
        width=video.get("width"),
        height=video.get("height"),
    )
    try:
        info.duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    return info


async def probe_video(path: Path) -> VideoInfo:
    _, stdout, _ = await run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
        "-of", "json", str(path),
    ], timeout=60)
    return parse_ffprobe(stdout.decode(errors="replace"))


def short_side_scale(max_short_side: int) -> str:
    """Ограничивает короткую сторону (720p и т.п.), не увеличивая и сохраняя чётные размеры."""
    m = max_short_side
    return (f"scale='if(lt(iw,ih),min({m},iw),-2)':'if(lt(iw,ih),-2,min({m},ih))'"
            f",scale=trunc(iw/2)*2:trunc(ih/2)*2")


def target_video_kbps(limit_bytes: int, duration: float, audio_kbps: int) -> float:
    total_kbps = limit_bytes * SIZE_SAFETY * 8 / duration / 1000
    return total_kbps - audio_kbps


def short_side_for_kbps(video_kbps: float) -> int:
    if video_kbps >= 2500:
        return 1080
    if video_kbps >= 1000:
        return 720
    return 480


class MediaTools:
    def __init__(self):
        self.threads = int(os.getenv("FFMPEG_THREADS", "2"))
        self.timeout = int(os.getenv("FFMPEG_TIMEOUT", "900"))

    async def _transcode(self, src: Path, dst: Path, info: VideoInfo,
                         video_args: List[str], vf: str, audio_kbps: int) -> bool:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               *video_args, "-vf", vf]
        cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info.has_audio else ["-an"]
        cmd += ["-movflags", "+faststart", "-threads", str(self.threads), str(dst)]

        async with _transcode_lock:
            try:
                code, _, stderr = await run(cmd, timeout=self.timeout)
            except asyncio.TimeoutError:
                logger.warning(f"ffmpeg: таймаут перекодирования {src.name}")
                return False
        if code != 0:
            logger.warning(f"ffmpeg: ошибка перекодирования {src.name}: {stderr.decode(errors='replace')[-300:]}")
            return False
        return True

    async def prepare_video(self, path: Path, limit_bytes: int) -> Tuple[Optional[Path], VideoInfo, bool]:
        """
        Приводит видео к H.264 и лимиту размера.
        Возвращает (путь или None, если не вышло; VideoInfo; было ли сжатие по размеру).
        """
        info = await probe_video(path)
        size = path.stat().st_size
        playable = info.vcodec in PLAYABLE_VIDEO_CODECS

        if size <= limit_bytes and playable:
            return path, info, False

        if size <= limit_bytes:
            dst = path.with_name(f"{path.stem}_h264.mp4")
            if await self._transcode(path, dst, info, ["-crf", "23"], "scale=trunc(iw/2)*2:trunc(ih/2)*2", 128) \
                    and dst.stat().st_size <= limit_bytes:
                return dst, await probe_video(dst), False

        if not info.duration:
            logger.warning(f"Сжатие {path.name}: неизвестна длительность")
            return None, info, False

        audio_kbps = 96 if info.has_audio else 0
        video_kbps = target_video_kbps(limit_bytes, info.duration, audio_kbps)
        if info.has_audio and video_kbps < 600:
            audio_kbps = 64
            video_kbps = target_video_kbps(limit_bytes, info.duration, audio_kbps)

        dst = path.with_name(f"{path.stem}_small.mp4")
        for _ in range(3):
            if video_kbps < MIN_VIDEO_KBPS:
                break
            kbps = int(video_kbps)
            ok = await self._transcode(
                path, dst, info,
                ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"],
                short_side_scale(short_side_for_kbps(video_kbps)), audio_kbps,
            )
            if not ok:
                break
            if dst.stat().st_size <= limit_bytes:
                logger.info(f"Сжато {path.name}: {size // 1024} → {dst.stat().st_size // 1024} КБ")
                return dst, await probe_video(dst), True
            video_kbps *= 0.8

        logger.warning(f"Не удалось сжать {path.name} до {limit_bytes // (1024 * 1024)} МБ")
        return None, info, False

    async def prepare_photo(self, path: Path, limit_bytes: int) -> Tuple[Optional[Path], bool]:
        """Приводит фото к JPEG/PNG и лимиту размера. Возвращает (путь или None; было ли сжатие)."""
        if path.stat().st_size <= limit_bytes and path.suffix.lower() in PHOTO_EXTENSIONS_AS_IS:
            return path, False

        compress = path.stat().st_size > limit_bytes
        dst = path.with_name(f"{path.stem}_tg.jpg")
        for side, quality in ((4096, 2), (2560, 3), (2048, 5), (1600, 8)):
            vf = f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease"
            try:
                code, _, _ = await run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                                        "-vf", vf, "-q:v", str(quality), "-frames:v", "1", str(dst)],
                                       timeout=60)
            except asyncio.TimeoutError:
                break
            if code != 0:
                break
            if dst.stat().st_size <= limit_bytes:
                return dst, compress

        logger.warning(f"Не удалось подготовить фото {path.name}")
        return None, False
