"""
Конфигурация для Telegram бота VoiceSticker
"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Базовые пути
BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage"
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"
CACHE_DIR = BASE_DIR / ".cache"

# Создаем директории если их нет
for directory in [STORAGE_DIR, LOGS_DIR, MODELS_DIR, CACHE_DIR]:
    directory.mkdir(exist_ok=True)

# MVP режим (упрощенная версия без ML моделей) - ВАЖНО: определяем до использования
MVP_MODE = os.getenv("MVP_MODE", "true").lower() == "true"

# Telegram Bot настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

# API для генерации изображений
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
if not REPLICATE_API_TOKEN and not MVP_MODE:
    raise ValueError("REPLICATE_API_TOKEN не найден для режима с AI!")

# OpenAI API для улучшения промптов
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # ← ИСПРАВЛЕНО!

# Google Gemini API для улучшения промптов
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# База данных
DATABASE_PATH = BASE_DIR / "bot_database.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# Настройки STT (Speech-to-Text)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
MAX_AUDIO_DURATION = 60  # секунд
MAX_AUDIO_SIZE = 20 * 1024 * 1024  # 20 MB

# Настройки генерации изображений
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "stabilityai/stable-diffusion-2-1-base")
SD_DEVICE = os.getenv("SD_DEVICE", "cpu")  # cpu, cuda, mps (для Mac M1/M2)
IMAGE_SIZE = (512, 512)
MAX_QUEUE_SIZE = 10
GENERATION_TIMEOUT = 120  # секунд

# Настройки стикеров
STICKER_MAX_SIZE = 512  # максимальный размер стороны в пикселях
STICKER_FILE_SIZE_LIMIT = 512 * 1024  # 512 KB для Telegram

# Rate limiting
RATE_LIMIT_MESSAGES_PER_MINUTE = 20
RATE_LIMIT_MESSAGES_PER_HOUR = 100

# Стили для генерации
AVAILABLE_STYLES = {
    "default": "cute sticker art style, kawaii",
    "cartoon": "cartoon style, animated, colorful",
    "anime": "anime style, chibi, manga",
    "minimal": "minimalist, simple, clean lines",
    "pixel": "pixel art, 8-bit style, retro",
    "3d": "3D render, volumetric, modern",
    "watercolor": "watercolor painting style, artistic",
    "custom": ""  # Пользовательский стиль
}

# Фоны для стикеров
BACKGROUND_STYLES = {
    "transparent": "Прозрачный",
    "white": "Белый",
    "gradient": "Градиент",
    "circle": "Круглый",
    "rounded": "Закругленные углы"
}

# Логирование
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = LOGS_DIR / "bot.log"

# Webhook настройки (для production)
WEBHOOK_ENABLED = os.getenv("WEBHOOK_ENABLED", "false").lower() == "true"
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))

# Настройки стикерпаков
BOT_USERNAME = os.getenv("BOT_USERNAME", "Voice_Stiker_Bot")  # Имя бота (без @) - замените на актуальное
MAX_PACKS_PER_USER = int(os.getenv("MAX_PACKS_PER_USER", "10"))  # Макс. кол-во паков на пользователя
MAX_STICKERS_PER_PACK = int(os.getenv("MAX_STICKERS_PER_PACK", "120"))  # Макс. кол-во стикеров в паке
DEFAULT_STICKER_EMOJI = os.getenv("DEFAULT_STICKER_EMOJI", "🎨")  # Эмодзи по умолчанию

# Режим разработки
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Настройки кэша
CACHE_TTL = 3600  # 1 час
CACHE_MAX_SIZE = 100  # максимум элементов в кэше

# Админы бота (Telegram user IDs)
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]

# Сообщения бота
MESSAGES = {
    "start": """👋 Привет! Я бот VoiceSticker!

🎨 Я помогу тебе создать уникальные стикеры из:
- 🎤 Голосовых сообщений
- 💬 Текстовых описаний

Просто отправь мне голосовое сообщение или текст, и я создам для тебя стикер!

Используй /help для справки.""",

    "help": """📖 Как использовать бота:

1️⃣ Отправь голосовое сообщение или текст
2️⃣ Выбери стиль стикера
3️⃣ Выбери фон
4️⃣ Получи готовый стикер!

📊 Команды:
/start - Начать работу
/help - Эта справка
/mystickers - Мои стикеры
/stats - Моя статистика""",

    "processing": "🎨 Создаю стикер... Это может занять несколько секунд.",
    "error": "😔 Произошла ошибка. Попробуйте еще раз.",
    "rate_limit": "⏱ Слишком много запросов. Подождите немного.",
    "choose_style": "🎨 Выберите стиль для стикера:",
    "choose_background": "🖼 Выберите фон для стикера:",
    "sticker_ready": "✅ Ваш стикер готов!",
    "no_stickers": "У вас пока нет созданных стикеров.",
}

# Проверка конфигурации
def validate_config():
    """Проверяет корректность конфигурации"""
    errors = []

    if not BOT_TOKEN:
        errors.append("BOT_TOKEN не установлен")

    if WEBHOOK_ENABLED and not WEBHOOK_HOST:
        errors.append("WEBHOOK_HOST требуется при WEBHOOK_ENABLED=true")

    if errors:
        raise ValueError(f"Ошибки конфигурации: {', '.join(errors)}")

# Выполняем проверку при импорте
if not DEBUG:
    validate_config()