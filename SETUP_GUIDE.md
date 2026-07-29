# 🚀 Установка и настройка

Инструкция по развёртыванию бота транскрибации на Ubuntu-сервере или
виртуальной машине.

---

### Рекомендуемая конфигурация ВМ

| Параметр | Минимум | **Рекомендуется** | Комментарий |
|----------|---------|-------------------|-------------|
| vCPU | 2 | **4** | Больше 4 почти не ускоряет |
| ОЗУ | 3 GB | **4 GB** | Замер: пик 1866 МБ + ОС ≈ 2.4 GB, запас 1.6 GB |
| Диск | 8 GB | **12 GB** | Модель 1.6 GB + silero 128 MB + torch 1.7 GB + ОС |
| ОС | Ubuntu 22.04 | **Ubuntu 24.04 LTS** | Python 3.12 из репозитория |
| Swap | 512 MB | **1 GB** | Страховка, в норме не используется |

### Настройки гипервизора

```
CPU type       = host-passthrough   # без него не будет AVX2 → падение в 2–3 раза
NUMA           = выкл
```

Проверка, что AVX2 доступен внутри ВМ:

```bash
lscpu | grep -o 'avx2\|avx512f\|fma' | sort -u
```

Если `avx2` не выводится — исправьте тип CPU в гипервизоре.

---

## 🎧 Выбор модели Whisper

| Модель | Диск | Комментарий |
|--------|------|-------------|
| **`large-v3-turbo`** | 1.6 GB | **Рекомендуется.** Вдвое быстрее `large-v3` при том же WER |
| `large-v3` | 2.9 GB | Смысла нет: медленнее и больше при равном качестве |
| `medium` | 769 MB | Только если ОЗУ меньше 3 GB; русский заметно слабее |
| `small` / `base` | 484 / 145 MB | Для русского непригодны |

У Systran нет официальной turbo-сборки, поэтому при `WHISPER_MODEL=large-v3-turbo`
код автоматически подставляет `deepdml/faster-whisper-large-v3-turbo-ct2`.

`WHISPER_MODEL` принимает три вида значений:

1. размер: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`
2. полный repo-id HuggingFace: `Systran/faster-whisper-large-v3`
3. путь к локальной CTranslate2-папке (внутри должен быть `model.bin`)

### Точность вычислений

`WHISPER_COMPUTE_TYPE` по умолчанию `int8` на CPU. Это заметно быстрее `float32`
при незначительной потере качества. Варианты: `int8`, `int8_float32`, `float32`
для CPU; `float16`, `int8_float16` для CUDA.

---

## ⚡ Установка

### 1. Системные пакеты

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip python3-venv ffmpeg htop wget curl
```

`ffmpeg` нужен faster-whisper для декодирования `.ogg` из Telegram.

### 2. Клонирование и виртуальное окружение

```bash
git clone https://github.com/keonji/tg-voice-bot.git
cd tg-voice-bot

python3 -m venv .venv
source .venv/bin/activate
```

### 3. Зависимости

**Важно:** torch ставится первым и обязательно из CPU-индекса. По умолчанию
`pip install torch` тянет CUDA-сборку и ~2.7 GB библиотек NVIDIA, которые
на CPU-сервере бесполезны — это разница в 4.4 GB на диске.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Проверка, что встала именно CPU-сборка:

```bash
python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# ожидается: 2.x.y+cpu False
```

### 4. Конфигурация

```bash
cp example.env .env
nano .env
```

Обязательно заполните:

```env
BOT_TOKEN=<токен от @BotFather>
ADMIN_USER_IDS=<ваш ID из @userinfobot>
```

`ADMIN_USER_IDS` нужен только для команды `/status` — транскрибация работает
для всех.

### 5. Проверка

```bash
python3 run_tests.py
```

Скрипт проверит синтаксис, разрешимость импортов и прогонит тесты.

### 6. Первый запуск

```bash
python3 bot.py
```

В логах должно появиться:

```
✅ Настроено администраторов: 1
🎯 Система готова к работе!
```

Модель Whisper (1.6 GB) скачается при первом голосовом сообщении, silero_te
(128 MB) — при первом восстановлении пунктуации. Первое сообщение поэтому
обрабатывается дольше обычного.

---

## 🔧 Автозапуск через systemd

### 1. Создание unit-файла

```bash
sudo vi /etc/systemd/system/telegram_bot.service
```

```ini
[Unit]
Description=Telegram Voice Transcription Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=telegram
WorkingDirectory=/home/telegram
ExecStart=/usr/bin/python3 /home/telegram/bot.py
Restart=always
RestartSec=10

# Пик потребления — Whisper large-v3-turbo (int8) + silero_te.
# Замер на 40 с речи: пик 1866 МБ RSS. Лимит с запасом ~40%.
MemoryMax=2600M

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Замените `User` и `WorkingDirectory` на свои значения. Если используете venv,
укажите в `ExecStart` путь к `/.venv/bin/python`.

### 2. Активация

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegram_bot.service
sudo systemctl status telegram_bot.service
```

### 3. Управление

```bash
sudo systemctl stop telegram_bot.service
sudo systemctl restart telegram_bot.service

# Логи в реальном времени
sudo journalctl -u telegram_bot.service -f

# Логи за последний час
sudo journalctl -u telegram_bot.service --since "1 hour ago"
```

---

## 🩺 Диагностика

### Проверка, что модель загружена

Команда `/status` в чате (от администратора) покажет модель, устройство,
точность вычислений и загружена ли модель в память.

### Типичные проблемы

| Симптом | Причина | Решение |
|---------|---------|---------|
| Бот «зависает» на первом голосовом | Скачивается модель Whisper (1.6 GB) | Подождите, смотрите логи |
| Очень медленная транскрибация | Нет AVX2 либо мало vCPU | Проверьте `lscpu` |
| `⚠️ Аудио слишком большое` | Лимит Telegram Bot API — 20 МБ на `getFile` | Записать короче или разбить на части |
| Текст без пунктуации | silero_te не скачался | Смотрите логи: `Не удалось инициализировать silero_te` |
| `Модель транскрибации недоступна` | Нет сети при первом запуске или битый кэш | Удалите `models/whisper` и перезапустите |
| Диск неожиданно занят на несколько GB | Установлена CUDA-сборка torch | Переставьте с `--index-url .../whl/cpu` |
| OOM при 3 GB ОЗУ | Пик 1866 МБ + ОС не влезают | Добавьте ОЗУ до 4 GB или `WHISPER_MODEL=medium` |

### Очистка

Временные аудиофайлы бот чистит сам каждые `CLEANUP_INTERVAL` часов и при
штатном завершении. Никаких других данных на диск он не пишет.

---

## 🎯 Оптимизация

### Минимальная ВМ (2 vCPU / 3 GB)

```env
WHISPER_MODEL=large-v3-turbo
WHISPER_BEAM_SIZE=1
PUNCTUATION_THREADS=2
```

При 3 GB ОЗУ запас над пиком всего ~500 МБ — держите `MemoryMax=2600M`
и следите за `memory.events`.

### Рекомендуемая ВМ (4 vCPU / 4 GB)

```env
WHISPER_MODEL=large-v3-turbo
WHISPER_BEAM_SIZE=5
PUNCTUATION_THREADS=4
```

### Приоритет скорости

```env
WHISPER_MODEL=large-v3-turbo
WHISPER_BEAM_SIZE=1
```

`beam_size=1` даёт +26% скорости на 8 ядрах при практически идентичной
расшифровке. Наращивать vCPU выше 4 малоэффективно: от 4 к 8 ядрам выигрыш
около 20%.

### Экономия ОЗУ ценой качества

Отключение пунктуации не загружает модель silero_te. Замер её изолированного
следа: `import torch` — 220 МБ, загрузка silero_te — ещё 163 МБ, пик при
обработке — 454 МБ. Экономится сама модель (~180 МБ) и часть пика; сам torch
всё равно импортируется зависимостями.

Текст при этом приходит менее читаемым — пунктуация останется только та,
которую даёт `initial_prompt` самого Whisper:

```env
ADD_PUNCTUATION=false
```
