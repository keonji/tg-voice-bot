import os
import logging
import wave
import json
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler
from telegram.ext import filters
from vosk import Model, KaldiRecognizer

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Устанавливаем уровень DEBUG для подробной информации
)
logger = logging.getLogger(__name__)

# Загрузка модели Vosk
if not os.path.exists("model"):
    logger.error("Пожалуйста, скачайте модель Vosk и поместите ее в папку 'model'.")
    exit(1)

model = Model("model")

async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        voice = update.message.voice
        user_name = update.message.from_user.full_name
        logger.info(f"Получено голосовое сообщение от {user_name}")

        # Скачиваем голосовое сообщение
        file = await context.bot.get_file(voice.file_id)
        file_path = 'voice.ogg'
        await file.download_to_drive(file_path)
        logger.info("Голосовое сообщение скачано")

        # Проверяем размер файла OGG
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            logger.error("Файл voice.ogg не существует или пустой")
            await update.message.reply_text(
                f"<b>{user_name}:</b>\n[Ошибка при скачивании голосового сообщения]",
                parse_mode='HTML'
            )
            return

        # Конвертируем OGG в WAV с частотой 16000 Гц и одним каналом
        command = [
            'ffmpeg', '-y', '-i', 'voice.ogg',
            '-ar', '16000',  # Устанавливаем частоту дискретизации
            '-ac', '1',      # Устанавливаем количество каналов
            'voice.wav'
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Голосовое сообщение конвертировано в WAV")

        # Проверяем размер файла WAV
        if not os.path.exists('voice.wav') or os.path.getsize('voice.wav') == 0:
            logger.error("Файл voice.wav не существует или пустой")
            await update.message.reply_text(
                f"<b>{user_name}:</b>\n[Ошибка при конвертации голосового сообщения]",
                parse_mode='HTML'
            )
            return

        ogg_size = os.path.getsize('voice.ogg')
        wav_size = os.path.getsize('voice.wav')
        logger.info(f"Размер voice.ogg: {ogg_size} байт")
        logger.info(f"Размер voice.wav: {wav_size} байт")

        # Распознаем речь с помощью Vosk
        try:
            wf = wave.open('voice.wav', 'rb')
        except Exception as e:
            logger.exception("Ошибка при открытии файла voice.wav")
            await update.message.reply_text(
                f"<b>{user_name}:</b>\n[Ошибка при обработке голосового сообщения]",
                parse_mode='HTML'
            )
            return

        rec = KaldiRecognizer(model, wf.getframerate())
        logger.info("Начато распознавание речи")

        results = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                logger.debug(f"Partial result: {res}")
                results.append(res.get('text', ''))
        res = json.loads(rec.FinalResult())
        logger.debug(f"Final result: {res}")
        results.append(res.get('text', ''))

        text = ' '.join(results).strip()
        logger.info(f"Распознавание завершено, полученный текст: '{text}'")

        # Удаляем временные файлы
        wf.close()
        os.remove('voice.ogg')
        os.remove('voice.wav')
        logger.info("Временные файлы удалены")

        # Проверяем, что текст не пустой
        if text:
            # Отправляем отформатированное сообщение
            response = f"<b>{user_name}:</b>\n{text}"
            await update.message.reply_text(response, parse_mode='HTML')
            logger.info("Отправлено сообщение с расшифровкой")
        else:
            logger.warning("Распознанный текст пустой")
            await update.message.reply_text(
                f"<b>{user_name}:</b>\n[Не удалось распознать речь]",
                parse_mode='HTML'
            )

    except Exception as e:
        logger.exception("Ошибка при обработке голосового сообщения")
        await update.message.reply_text(
            f"<b>{user_name}:</b>\n[Произошла ошибка при обработке сообщения]",
            parse_mode='HTML'
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот активен и готов расшифровывать голосовые сообщения!")

def main():
    # Замените 'YOUR_TELEGRAM_BOT_TOKEN' на токен вашего бота
    app = ApplicationBuilder().token("YOUR_TELEGRAM_BOT_TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    # Запускаем бота
    app.run_polling(close_loop=False)

if __name__ == '__main__':
    main()
