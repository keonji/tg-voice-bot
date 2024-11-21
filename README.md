
## Telegram бот, который расшифровывает голосовые сообщения и отправляет их в виде отформатированного текста с указанием имени отправителя.

#### Установите нужное окружение:
```
pip install python-telegram-bot vosk
sudo apt-get install ffmpeg
wget https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip
unzip vosk-model-ru-0.42.zip
mv vosk-model-ru-0.42 model
```

Замените 'YOUR_TELEGRAM_BOT_TOKEN' на токен вашего бота, полученный от @BotFather.

#### Запуск:
```
python3 bot.py
```

Требуется минимум 8gb RAM

#### Автозапуск:
Файл сервиса /etc/systemd/system/telegram_voice_bot.service:
```
[Unit]
Description=Telegram Voice Bot
After=network.target

[Service]
User=yourusername
WorkingDirectory=/path/to/your/bot
ExecStart=/usr/bin/python3 /path/to/your/bot/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

User: замените на ваше имя пользователя.
WorkingDirectory и ExecStart: замените на путь к вашему скрипту.

##### Активируйте и запустите сервис:
```
sudo systemctl daemon-reload
sudo systemctl enable telegram_voice_bot.service
sudo systemctl start telegram_voice_bot.service
```
