"""
Обработчики команд и сообщений для бота VoiceSticker
Версия с улучшенной точностью, системой обратной связи, навигацией и поддержкой текста на стикерах
"""
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import html

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, InputFile, FSInputFile,
    ReplyKeyboardRemove
)
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from logger import logger, log_user_action, log_error
from config import (
    MESSAGES,
    AVAILABLE_STYLES,
    STORAGE_DIR, ADMIN_IDS, RATE_LIMIT_MESSAGES_PER_MINUTE,
    BOT_USERNAME, MAX_PACKS_PER_USER, MAX_STICKERS_PER_PACK
)
from db_manager import db_manager
from image_generation_service import image_service
from stt_service import stt_service
from sticker_utils import process_sticker
from telegram_sticker_manager import sticker_manager


def escape_html(text: str) -> str:
    """Экранирует HTML символы в тексте"""
    return html.escape(str(text))


# Вспомогательная функция для подсчета стикеров
async def get_pack_sticker_count(bot: Bot, pack_name: str) -> int:
    """Получает количество стикеров в паке"""
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
        return len(sticker_set.stickers)
    except Exception:
        return 0


# Состояния FSM
class StickerGeneration(StatesGroup):
    """Состояния для процесса генерации стикера"""
    waiting_for_prompt = State()
    waiting_for_voice = State()
    waiting_for_style = State()
    waiting_for_text = State()  # Новое состояние для текста
    processing = State()
    waiting_for_feedback = State()
    waiting_for_feedback_comment = State()


# Инициализация диспетчера
dp = Dispatcher()

# Хранилище для временных данных генерации
generation_cache: Dict[int, Dict] = {}

# Защита от дублирования при добавлении в пак
processing_stickers = set()

# Названия стилей для отображения
STYLE_NAMES = {
    "cartoon": "🎨 Cartoon",
    "anime": "🎭 Anime",
    "realistic": "📸 Realistic",
    "minimalist": "⚡ Минимализм",
    "pixel": "🎮 Пиксель арт",
    "cute": "💞 Cute"
}


# Вспомогательные функции
def create_sticker_actions_keyboard(sticker_id: int, has_text: bool = False) -> InlineKeyboardMarkup:
    """Создает клавиатуру с действиями для стикера"""
    builder = InlineKeyboardBuilder()

    # Первая строка - основные действия
    builder.button(text="📦 В стикерпак", callback_data=f"add_to_pack:{sticker_id}")
    builder.button(text="🔄 Создать заново", callback_data=f"retry:{sticker_id}")

    # Вторая строка - модификация текста (если применимо)
    if has_text:
        builder.button(text="✏️ Изменить текст", callback_data=f"change_text:{sticker_id}")
    else:
        builder.button(text="➕ Добавить текст", callback_data=f"add_text_to:{sticker_id}")

    # Третья строка - оценка
    builder.button(text="👍", callback_data=f"rate:{sticker_id}:5")
    builder.button(text="👎", callback_data=f"rate:{sticker_id}:1")

    # Четвертая строка - навигация
    builder.button(text="➕ Новый стикер", callback_data="new_sticker")
    builder.button(text="🏠 Меню", callback_data="main_menu")

    # Размещение кнопок
    if has_text:
        builder.adjust(2, 1, 2, 2)
    else:
        builder.adjust(2, 1, 2, 2)

    return builder.as_markup()


def create_text_option_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру с опцией добавления текста"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Добавить текст", callback_data="add_text")
    builder.button(text="✅ Без текста", callback_data="no_text")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 1)
    return builder.as_markup()


def create_text_position_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора позиции текста"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📍 Сверху", callback_data="text_pos:top")
    builder.button(text="🎯 По центру", callback_data="text_pos:center")
    builder.button(text="📍 Снизу", callback_data="text_pos:bottom")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(3, 1)
    return builder.as_markup()


def create_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает главное меню с кнопкой стикерпаков"""
    builder = InlineKeyboardBuilder()

    # Главная кнопка
    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.adjust(1)

    # Функциональные кнопки
    builder.button(text="📊 Статистика", callback_data="stats")
    builder.button(text="🖼 Мои стикеры", callback_data="my_stickers")
    builder.button(text="📦 Мои паки", callback_data="my_packs")
    builder.button(text="📝 Примеры", callback_data="show_examples")
    builder.button(text="🎨 Стили", callback_data="show_styles")
    builder.button(text="❓ Помощь", callback_data="help")
    builder.button(text="💡 Советы", callback_data="tips")

    # Размещаем кнопки
    builder.adjust(1, 3, 3, 1)

    return builder.as_markup()


def create_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопкой возврата в меню"""
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ В главное меню", callback_data="back_to_menu")
    return builder.as_markup()


def create_style_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора стиля"""
    buttons = []
    # Обновлен список стилей согласно optimized_sticker_generation_service.py
    styles = [
        ("🎨 Cartoon", "cartoon"),
        ("🎭 Anime", "anime"),
        ("📸 Realistic", "realistic"),
        ("⚡ Минимализм", "minimalist"),
        ("🎮 Пиксель арт", "pixel"),
        ("💞 Cute", "cute")
    ]

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(styles), 2):
        row = []
        for j in range(2):
            if i + j < len(styles):
                name, value = styles[i + j]
                row.append(InlineKeyboardButton(
                    text=name,
                    callback_data=f"style:{value}"
                ))
        buttons.append(row)

    # Добавляем кнопку "Назад"
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_emoji_for_prompt(prompt: str) -> str:
    """Определяет подходящий эмодзи для стикера на основе промпта"""
    prompt_lower = prompt.lower()

    # Словарь ключевых слов и эмодзи
    emoji_map = {
        # Эмоции
        "радост": "😊", "счаст": "😊", "весел": "😄", "happy": "😊",
        "груст": "😢", "печал": "😢", "sad": "😢",
        "злой": "😠", "сердит": "😠", "angry": "😠",
        "любовь": "❤️", "люблю": "❤️", "love": "❤️",
        "смех": "😂", "смеш": "😂", "lol": "😂",
        "удивл": "😮", "wow": "😮", "вау": "😮",

        # Животные
        "кот": "🐱", "кошк": "🐱", "cat": "🐱",
        "собак": "🐶", "пес": "🐶", "dog": "🐶",
        "медве": "🐻", "bear": "🐻",
        "заяц": "🐰", "кролик": "🐰", "rabbit": "🐰",
        "свин": "🐷", "pig": "🐷",

        # Действия
        "привет": "👋", "hello": "👋", "hi": "👋",
        "пока": "👋", "bye": "👋",
        "спасибо": "🙏", "thanks": "🙏",
        "ок": "👌", "ok": "👌", "хорошо": "👌",

        # Праздники
        "рожден": "🎂", "birthday": "🎂",
        "новый год": "🎄", "new year": "🎄",
        "праздник": "🎉", "party": "🎉",

        # Еда
        "кофе": "☕", "coffee": "☕",
        "пицца": "🍕", "pizza": "🍕",
        "торт": "🍰",

        # Природа
        "солнце": "☀️", "sun": "☀️",
        "дождь": "🌧️", "rain": "🌧️",
        "цвет": "🌸", "flower": "🌸",

        # Профессии/занятия
        "программ": "💻", "код": "💻", "code": "💻",
        "учи": "📚", "study": "📚",
        "работ": "💼", "work": "💼",
    }

    # Ищем ключевые слова в промпте
    for keyword, emoji in emoji_map.items():
        if keyword in prompt_lower:
            return emoji

    # Если ничего не найдено, возвращаем универсальный эмодзи
    return "🎨"


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    # Очищаем состояние
    await state.clear()

    # Проверяем/добавляем пользователя
    await db_manager.add_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        language_code=message.from_user.language_code
    )

    await message.answer(
        f"👋 <b>Привет, {message.from_user.first_name}!</b>\n\n"
        f"Я помогу создать уникальные стикеры из текста или голоса!\n\n"
        f"🆕 <b>Новое:</b> Теперь можно добавлять текст на стикеры!\n\n"
        f"Просто отправь мне описание стикера или голосовое сообщение 🎤",
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    log_user_action(message.from_user.id, "help_command")

    # Обновленный текст HELP_TEXT
    help_text = """
🎯 <b>Как создать стикер:</b>

1️⃣ Отправьте текст или голосовое сообщение
2️⃣ Выберите стиль
3️⃣ Добавьте текст (опционально)
4️⃣ Получите готовый стикер!

✏️ <b>Добавление текста:</b>
• После выбора стиля бот спросит про текст
• Максимум 20 символов
• Можно выбрать позицию: сверху, снизу, по центру

💡 <b>Советы:</b>
• Будьте конкретны: "радостный кот" лучше чем просто "кот"
• Указывайте действия: "кот играет на гитаре"
• Добавляйте эмоции: "грустный", "веселый", "злой"
• Для фона укажите место: "кот в космосе", "собака в парке"

🎨 <b>Доступные стили:</b>
• <b>Cartoon</b> - мультяшный стиль
• <b>Anime</b> - аниме стиль
• <b>Realistic</b> - реалистичный
• <b>Pixel</b> - пиксельная графика
• <b>Minimalist</b> - минимализм
• <b>Cute</b> - милый стиль

📦 <b>Стикерпаки:</b>
Вы можете добавлять созданные стикеры в настоящие Telegram стикерпаки!

<b>Полезные команды:</b>
/help - это сообщение
/menu - главное меню
/mystats - ваша статистика
/mypacks - ваши стикерпаки
/check_pack - проверить стикерпаки
/clean_packs - очистить недействительные паки
"""

    await message.answer(
        help_text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Обработчик команды /stats с расширенной статистикой по стикерпакам"""
    user_id = message.from_user.id
    log_user_action(user_id, "stats_command")

    general_stats = await db_manager.get_user_stats(user_id)
    if not general_stats:
        await message.answer("❌ Ошибка получения общей статистики.")
        return

    sticker_pack_stats = await db_manager.get_user_sticker_stats(user_id)
    if not sticker_pack_stats:
        sticker_pack_stats = {
            "total_stickers": 0,
            "stickers_in_packs": 0,
            "total_packs": 0,
            "stickers_not_in_packs": 0
        }

    ratings_stats = await db_manager.fetchone(
        """
        SELECT
            AVG(rating) as avg_rating,
            COUNT(CASE WHEN rating >= 4 THEN 1 END) as good_ratings,
            COUNT(CASE WHEN rating IS NOT NULL THEN 1 END) as total_rated
        FROM stickers
        WHERE user_id = ? AND is_deleted = 0
        """,
        (user_id,)
    )

    # Статистика по стикерам с текстом
    text_stats = await db_manager.fetchone(
        """
        SELECT COUNT(*) as stickers_with_text
        FROM stickers
        WHERE user_id = ? AND is_deleted = 0
        AND metadata LIKE '%"text":%' AND metadata NOT LIKE '%"text": null%'
        """,
        (user_id,)
    )

    registered = datetime.fromisoformat(general_stats['registered_at']).strftime("%d.%m.%Y")

    total_rated = ratings_stats['total_rated'] or 0
    good_ratings = ratings_stats['good_ratings'] or 0
    avg_rating = ratings_stats['avg_rating'] or 0.0
    success_percent = (good_ratings / total_rated * 100) if total_rated > 0 else 0

    text = f"""📊 <b>Ваша статистика:</b>

👤 Имя: {general_stats['first_name'] or 'Не указано'}
📅 Дата регистрации: {registered}

🎨 Создано стикеров: {general_stats['total_stickers']}
✏️ С текстом: {text_stats['stickers_with_text']}
🎤 Голосовых сообщений: {general_stats['total_voice_messages']}
💬 Текстовых сообщений: {general_stats['total_text_messages']}

<b>📦 Стикерпаки:</b>
• Всего паков: {sticker_pack_stats['total_packs']}
• Стикеров в паках: {sticker_pack_stats['stickers_in_packs']}
• Стикеров не в паках: {sticker_pack_stats['stickers_not_in_packs']}

<b>⭐ Качество генерации:</b>
• Средняя оценка: {avg_rating:.1f}/5.0
• Успешных генераций: {good_ratings}/{total_rated} ({success_percent:.0f}%)
"""

    await message.answer(
        text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("tips"))
async def cmd_tips(message: Message):
    """Советы для улучшения результатов"""
    tips_text = """💡 <b>Советы для идеальных стикеров:</b>

<b>1. Будьте конкретны:</b>
❌ "кот"
✅ "рыжий кот с зелёными глазами"

<b>2. Опишите эмоции:</b>
❌ "собака"
✅ "радостная собака виляет хвостом"

<b>3. Добавьте действие:</b>
❌ "программист"
✅ "программист печатает код с кофе"

<b>4. Укажите стиль одежды/детали:</b>
❌ "девушка"
✅ "девушка в красном платье с цветами"

<b>5. Используйте цвета:</b>
❌ "дракон"
✅ "синий дракон с золотыми крыльями"

<b>✏️ Текст на стикерах:</b>
• Короткие слова работают лучше (LOL, OMG, WOW)
• Максимум 20 символов
• Текст добавляется после генерации

<b>🎯 Секрет:</b> Представьте, что описываете картинку человеку по телефону!"""

    await message.answer(
        tips_text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


# Обработчики callback кнопок
@dp.callback_query(F.data == "create_sticker")
async def cb_create_sticker(callback: CallbackQuery, state: FSMContext):
    """Начало создания стикера"""
    await callback.answer()

    await state.clear()
    await state.set_state(StickerGeneration.waiting_for_prompt)

    try:
        await callback.message.edit_text(
            "🎨 <b>Создание стикера</b>\n\n"
            "Отправьте мне:\n"
            "• 🎤 Голосовое сообщение\n"
            "• 💬 Текстовое описание\n\n"
            "<b>Примеры хороших описаний:</b>\n"
            "• <i>«Веселый котик с радугой»</i>\n"
            "• <i>«Грустный робот под дождем»</i>\n"
            "• <i>«Злой босс в костюме кричит»</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        # Если не можем отредактировать, отправляем новое сообщение
        await callback.message.answer(
            "🎨 <b>Создание стикера</b>\n\n"
            "Отправьте мне:\n"
            "• 🎤 Голосовое сообщение\n"
            "• 💬 Текстовое описание\n\n"
            "<b>Примеры хороших описаний:</b>\n"
            "• <i>«Веселый котик с радугой»</i>\n"
            "• <i>«Грустный робот под дождем»</i>\n"
            "• <i>«Злой босс в костюме кричит»</i>",
            parse_mode=ParseMode.HTML
        )


@dp.callback_query(F.data.startswith("style:"))
async def cb_select_style(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора стиля и спрашиваем про текст"""
    try:
        await callback.answer()

        # Сохраняем выбранный стиль
        style = callback.data.replace("style:", "")
        await state.update_data(style=style)

        # Получаем данные
        data = await state.get_data()
        prompt = data.get('prompt', '')

        # Спрашиваем про текст
        await callback.message.edit_text(
            f"📝 <b>Добавить текст на стикер?</b>\n\n"
            f"<b>Стиль:</b> {STYLE_NAMES.get(style, style)}\n"
            f"<b>Описание:</b> <i>{prompt[:50]}{'...' if len(prompt) > 50 else ''}</i>\n\n"
            "Текст будет добавлен в нижней части стикера",
            parse_mode=ParseMode.HTML,
            reply_markup=create_text_option_keyboard()
        )

    except Exception as e:
        logger.error(f"Error in style selection: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("rate:"))
async def cb_rate_sticker(callback: CallbackQuery, state: FSMContext):
    """Обработка оценки стикера"""
    parts = callback.data.split(":")
    sticker_id = int(parts[1])
    rating = int(parts[2])

    await db_manager.update_sticker_rating(sticker_id, callback.from_user.id, rating)

    user_cache = generation_cache.get(callback.from_user.id)
    if user_cache and user_cache['sticker_id'] == sticker_id:
        if hasattr(image_service, 'record_feedback'):
            image_service.record_feedback(user_cache['metadata'], rating)

    rating_responses = {
        5: "🎉 Отлично! Рад, что стикер получился идеальным!",
        4: "😊 Хорошо! Буду стараться делать ещё лучше!",
        3: "🤔 Понял, буду улучшаться!",
        2: "😕 Жаль, что не очень получилось. Попробуйте ещё раз с более детальным описанием.",
        1: "😔 Извините, что не оправдал ожиданий. Давайте попробуем снова?"
    }

    # Получаем метаданные стикера чтобы узнать есть ли текст
    sticker_data = await db_manager.fetchone(
        "SELECT metadata FROM stickers WHERE id = ?",
        (sticker_id,)
    )

    has_text = False
    if sticker_data and sticker_data['metadata']:
        metadata = json.loads(sticker_data['metadata'])
        if isinstance(metadata, dict):  # Ensure metadata is a dict before trying to get a key
            has_text = bool(metadata.get('text'))

    new_caption = (
        f"⭐ Ваша оценка: {'⭐' * rating}\n"
        f"{rating_responses[rating]}"
    )

    try:
        # Пробуем отредактировать caption или отправить новое сообщение
        # The original code here tried to edit the caption, but stickers sent
        # without a caption (as done in generate_final_sticker now) cannot have
        # their caption edited. It's safer to always send a new message.
        await callback.message.reply(
            new_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=create_sticker_actions_keyboard(sticker_id, has_text=has_text)
        )
    except Exception as e:
        logger.error(f"Failed to reply with rating message: {e}")
        await callback.message.answer(
            new_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=create_sticker_actions_keyboard(sticker_id, has_text=has_text)
        )

    await state.clear()
    await callback.answer("Спасибо за оценку!")


@dp.callback_query(F.data.startswith("retry:"))
async def cb_retry_sticker(callback: CallbackQuery, state: FSMContext):
    """Повторная генерация стикера"""
    try:
        sticker_id = int(callback.data.split(":")[1])

        sticker_data = await db_manager.fetchone(
            "SELECT prompt, style FROM stickers WHERE id = ?",
            (sticker_id,)
        )

        if sticker_data:
            # Получаем исходный prompt
            original_prompt = sticker_data['prompt']
            original_style = sticker_data['style']

            await state.update_data(
                prompt=original_prompt,
                retry_from=sticker_id
            )

            await callback.message.answer(
                f"🔄 <b>Создаю стикер заново</b>\n\n"
                f"Запрос: <i>{original_prompt}</i>\n\n"
                f"💡 <b>Совет:</b> Попробуйте выбрать другой стиль или добавить больше деталей!\n\n"
                f"Выберите стиль (предыдущий: {original_style}):",
                reply_markup=create_style_keyboard(),
                parse_mode=ParseMode.HTML
            )

            await state.set_state(StickerGeneration.waiting_for_style)
            await callback.answer()
        else:
            await callback.answer("Стикер не найден", show_alert=True)

    except Exception as e:
        logger.error(f"Error in cb_retry_sticker: {e}")
        await db_manager.log_error(
            error_type="callback_error",
            error_message=str(e),
            user_id=callback.from_user.id,
            context=f"retry_sticker:{sticker_id}"
        )
        await callback.answer("Произошла ошибка при повторной генерации", show_alert=True)


@dp.message(StateFilter(StickerGeneration.waiting_for_prompt), F.text)
async def handle_text_prompt(message: Message, state: FSMContext):
    """Обработка текстового описания"""
    prompt = message.text.strip()

    if len(prompt) < 3:
        await message.answer("❌ Слишком короткое описание. Минимум 3 символа.")
        return

    if len(prompt) > 200:
        await message.answer("❌ Слишком длинное описание. Максимум 200 символов.")
        return

    await state.update_data(
        prompt=prompt,
        user_id=message.from_user.id  # Сохраняем user_id при первом вводе
    )

    await db_manager.update_message_stats(message.from_user.id, is_voice=False)

    await state.set_state(StickerGeneration.waiting_for_style)

    await message.answer(
        f"📝 Отлично! Ваш запрос: <i>{prompt}</i>\n\n"
        f"🎨 <b>Выберите стиль стикера:</b>",
        reply_markup=create_style_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(StateFilter(StickerGeneration.waiting_for_prompt), F.voice)
async def handle_voice_prompt(message: Message, state: FSMContext, bot: Bot):
    """Обработка голосового сообщения"""
    voice = message.voice

    if voice.file_size > 20 * 1024 * 1024:
        await message.answer("❌ Голосовое сообщение слишком большое. Максимум 20 МБ.")
        return

    processing_msg = await message.answer("🎤 Распознаю речь...")

    try:
        file_info = await bot.get_file(voice.file_id)
        file_path = STORAGE_DIR / f"voice_{message.from_user.id}_{voice.file_id}.ogg"

        await bot.download_file(file_info.file_path, file_path)

        result = await stt_service.transcribe_audio(str(file_path))

        if not result or not result.get("text"):
            await processing_msg.edit_text("❌ Не удалось распознать речь. Попробуйте еще раз.")
            return

        prompt = result["text"]

        await state.update_data(
            prompt=prompt,
            user_id=message.from_user.id  # Сохраняем user_id при первом вводе
        )

        await db_manager.update_message_stats(message.from_user.id, is_voice=True)

        try:
            await processing_msg.delete()
        except Exception:
            pass

        await message.answer(
            f"🎤 Распознано: <i>{prompt}</i>\n\n"
            f"🎨 <b>Выберите стиль стикера:</b>",
            reply_markup=create_style_keyboard(),
            parse_mode=ParseMode.HTML
        )

        await state.set_state(StickerGeneration.waiting_for_style)

        try:
            os.unlink(file_path)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        try:
            await processing_msg.edit_text(MESSAGES["error"])
        except Exception:
            await message.answer(MESSAGES["error"])


@dp.message(StateFilter(StickerGeneration.waiting_for_feedback_comment), F.text)
async def handle_feedback_comment(message: Message, state: FSMContext):
    """Обработка комментария к стикеру"""
    comment = message.text.strip()
    data = await state.get_data()
    sticker_id = data.get('comment_sticker_id')

    if sticker_id:
        await db_manager.execute(
            "UPDATE stickers SET feedback_comment = ? WHERE id = ?",
            (comment, sticker_id)
        )

        await message.answer(
            "✅ <b>Спасибо за отзыв!</b>\n\n"
            "Это поможет мне улучшить генерацию стикеров.",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )

    await state.clear()


@dp.message(F.text)
async def handle_any_text(message: Message, state: FSMContext):
    """Обработка любого текста вне состояний"""
    current_state = await state.get_state()

    if not current_state:
        await state.update_data(
            prompt=message.text.strip(),
            user_id=message.from_user.id  # Сохраняем user_id при первом вводе
        )
        await state.set_state(StickerGeneration.waiting_for_style)

        await message.answer(
            f"💡 Создаю стикер из вашего текста:\n<i>«{message.text}»</i>\n\n"
            f"🎨 <b>Выберите стиль:</b>",
            reply_markup=create_style_keyboard(),
            parse_mode=ParseMode.HTML
        )


@dp.message(F.voice)
async def handle_any_voice(message: Message, state: FSMContext):
    """Обработка голосовых вне состояний"""
    current_state = await state.get_state()

    if not current_state:
        await state.set_state(StickerGeneration.waiting_for_prompt)
        await handle_voice_prompt(message, state, message.bot)


# Добавьте эту функцию в bot_handlers.py для тестирования:
@dp.message(Command("test_db"))
async def cmd_test_db(message: Message):
    """Тестирование сохранения в БД"""
    # Создаем тестовый стикер
    test_id = await db_manager.save_sticker(
        user_id=message.from_user.id,
        prompt="test sticker",
        style="test",
        file_path="/tmp/test.png",
        metadata=json.dumps({"test": True})
    )

    await message.answer(f"Saved with ID: {test_id}")

    # Проверяем
    check = await db_manager.fetchone(
        "SELECT * FROM stickers WHERE id = ?",
        (test_id,)
    )

    if check:
        await message.answer(f"Found in DB: {check}")
    else:
        await message.answer("NOT FOUND in DB!")

    # Удаляем тестовый стикер
    await db_manager.execute(
        "DELETE FROM stickers WHERE id = ?",
        (test_id,)
    )


# Вспомогательные функции
async def show_user_stickers(user_id: int, message: Message):
    """Показывает стикеры пользователя с оценками"""
    stickers = await db_manager.get_user_stickers(user_id, limit=10)

    if not stickers:
        await message.answer(
            "📭 <b>У вас пока нет стикеров</b>\n\n"
            "Создайте свой первый стикер прямо сейчас!",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    text = "🖼 <b>Ваши последние стикеры:</b>\n\n"

    for i, sticker in enumerate(stickers, 1):
        created = datetime.fromisoformat(sticker['created_at']).strftime("%d.%m %H:%M")
        rating = sticker.get('rating', 0)
        rating_text = f" {'⭐' * rating}" if rating else " (без оценки)"

        # Проверяем есть ли текст
        has_text = ""
        if sticker.get('metadata'):
            try:
                metadata = json.loads(sticker['metadata'])
                if metadata.get('text'):
                    has_text = " ✏️"
            except Exception:
                pass

        escaped_prompt = escape_html(sticker['prompt'][:30])
        text += f"{i}. {escaped_prompt}...{rating_text}{has_text} ({created})\n"

    text += "\n💡 <i>Оценивайте стикеры, чтобы я мог создавать их точнее!</i>"

    builder = InlineKeyboardBuilder()
    builder.button(text="🎨 Создать новый", callback_data="create_sticker")
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await message.answer(
        text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )


# Обработчики для команд меню
@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    """Показать главное меню"""
    await message.answer(
        "🎨 <b>Главное меню</b>\n\n"
        "Выберите действие или отправьте мне описание для стикера:",
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("mystats"))
async def cmd_mystats(message: Message):
    """Псевдоним для /stats"""
    await cmd_stats(message)


@dp.message(Command("mypacks"))
async def cmd_my_packs(message: Message):
    """Показывает стикерпаки пользователя"""
    user_id = message.from_user.id

    packs = await db_manager.get_user_packs(user_id)

    if not packs:
        await message.answer(
            "📦 <b>У вас пока нет стикерпаков</b>\n\n"
            "Создайте стикер и добавьте его в пак!",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    text = "📦 <b>Ваши стикерпаки:</b>\n\n"

    builder = InlineKeyboardBuilder()

    for i, pack in enumerate(packs, 1):
        pack_link = sticker_manager.get_pack_link(pack['pack_name'])
        text += f"{i}. Стикерпак №{i} - {pack['stickers_count']} стикеров\n"
        builder.button(
            text=f"📦 Пак №{i}",
            url=pack_link
        )

    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await message.answer(
        text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("mystickers"))
async def cmd_mystickers(message: Message):
    """Обработчик команды /mystickers"""
    await show_user_stickers(message.from_user.id, message)


@dp.message(Command("check_pack"))
async def cmd_check_pack(message: Message, bot: Bot):
    """Проверяет стикерпак пользователя и синхронизирует с БД"""
    user_id = message.from_user.id

    # Получаем паки пользователя
    packs = await db_manager.get_user_packs(user_id)

    if not packs:
        await message.answer(
            "📦 У вас нет стикерпаков",
            reply_markup=create_main_menu_keyboard()
        )
        return

    text = "📦 <b>Проверка ваших стикерпаков:</b>\n\n"

    for pack in packs:
        pack_name = pack['pack_name']
        try:
            # Пытаемся получить информацию о паке
            sticker_set = await bot.get_sticker_set(pack_name)
            text += f"✅ {pack_name}\n"
            text += f"   Стикеров: {len(sticker_set.stickers)}\n"
            text += f"   Ссылка: {sticker_manager.get_pack_link(pack_name)}\n\n"
        except Exception as e:
            if "STICKERSET_INVALID" in str(e):
                text += f"❌ {pack_name} - пак недействителен\n\n"
                # Удаляем недействительный пак из БД
                await db_manager.execute(
                    "DELETE FROM user_sticker_packs WHERE user_id = ? AND pack_name = ?",
                    (user_id, pack_name)
                )
            else:
                text += f"⚠️ {pack_name} - ошибка проверки\n\n"

    text += "💡 <i>Недействительные паки были удалены из БД</i>"

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=create_main_menu_keyboard()
    )


@dp.message(Command("clean_packs"))
async def cmd_clean_packs(message: Message, bot: Bot):
    """Очищает недействительные стикерпаки из БД"""
    user_id = message.from_user.id

    processing_msg = await message.answer(
        "🔍 <b>Проверяю ваши стикерпаки...</b>",
        parse_mode=ParseMode.HTML
    )

    # Очищаем недействительные паки
    removed_count = await sticker_manager.cleanup_invalid_packs(bot, user_id)

    try:
        await processing_msg.delete()
    except Exception:
        pass

    if removed_count > 0:
        text = (
            f"🧹 <b>Очистка завершена!</b>\n\n"
            f"Удалено недействительных паков: {removed_count}\n\n"
            "Теперь вы можете создавать новые стикеры."
        )
    else:
        text = "✅ <b>Все ваши паки действительны!</b>\n\nОчистка не требуется."

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=create_main_menu_keyboard()
    )


@dp.callback_query(F.data.startswith("refresh_pack:"))
async def cb_refresh_pack(callback: CallbackQuery, bot: Bot):
    """Обновляет информацию о стикерпаке"""
    pack_name = callback.data.split(":")[1]

    await callback.answer("🔄 Обновляю информацию о паке...")

    # Инструкция для пользователя
    text = (
        "🔄 <b>Как обновить стикерпак:</b>\n\n"
        "1️⃣ Откройте @Stickers\n"
        "2️⃣ Отправьте команду /cancel\n"
        "3️⃣ Нажмите на ссылку пака ниже\n"
        "4️⃣ Удалите пак и добавьте заново\n\n"
        "Или просто подождите 5-10 минут - Telegram обновит кэш автоматически.\n\n"
        f"📦 Ваш пак: {pack_name}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📦 Открыть пак",
        url=sticker_manager.get_pack_link(pack_name)
    )
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await callback.message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


# Обработчик для админских команд
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админская панель с расширенной статистикой"""
    if message.from_user.id not in ADMIN_IDS:
        return

    stats = await db_manager.get_total_stats()

    total_packs_count = (await db_manager.fetchone("SELECT COUNT(*) FROM user_sticker_packs"))[0]
    total_stickers_in_packs = (await db_manager.fetchone("SELECT COUNT(*) FROM sticker_pack_items"))[0]

    # Статистика по стикерам с текстом
    text_stats = await db_manager.fetchone(
        """
        SELECT COUNT(*) as stickers_with_text
        FROM stickers
        WHERE is_deleted = 0
        AND metadata LIKE '%"text":%' AND metadata NOT LIKE '%"text": null%'
        """
    )

    ratings = await db_manager.fetchall(
        """
        SELECT
            rating,
            COUNT(*) as count
        FROM stickers
        WHERE rating IS NOT NULL
        GROUP BY rating
        ORDER BY rating DESC
        """
    )

    ratings_text = "\n".join([f"{r['rating']}⭐: {r['count']} стикеров" for r in ratings])

    text = f"""👨‍💼 <b>Админ-панель</b>

📊 Общая статистика:
- Пользователей: {stats['total_users']}
- Стикеров создано: {stats['total_stickers']}
- С текстом: {text_stats['stickers_with_text']}
- Активных за 24ч: {stats['active_users_24h']}
- Средняя оценка: {stats['average_rating']}/5.0

📦 Статистика по пакам:
- Всего паков: {total_packs_count}
- Стикеров в паках (уникальных): {total_stickers_in_packs}

⭐ Распределение оценок:
{ratings_text}

📈 Качество генерации постоянно улучшается!
"""

    await message.answer(text, parse_mode=ParseMode.HTML)


# Экспортируем диспетчер
__all__ = ["dp"]


@dp.callback_query(F.data == "add_text")
async def cb_add_text(callback: CallbackQuery, state: FSMContext):
    """Запрашивает текст для стикера"""
    await callback.answer()

    await callback.message.edit_text(
        "✏️ <b>Введите текст для стикера</b>\n\n"
        "📏 Максимум 20 символов\n"
        "🔤 Лучше использовать короткие слова\n\n"
        "<b>Примеры хороших текстов:</b>\n"
        "• LOL\n"
        "• ОМГ!\n"
        "• Привет\n"
        "• WOW\n"
        "• Love ❤️\n"
        "• Хаха",
        parse_mode=ParseMode.HTML
    )

    await state.set_state(StickerGeneration.waiting_for_text)


@dp.callback_query(F.data == "no_text")
async def cb_no_text(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Создает стикер без текста"""
    await callback.answer()
    # Сохраняем user_id в state
    await state.update_data(
        sticker_text=None,
        user_id=callback.from_user.id  # Добавляем user_id
    )
    await generate_final_sticker(callback.message, state, bot)


@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена текущей операции"""
    await callback.answer()
    await state.clear()

    await callback.message.edit_text(
        "❌ Операция отменена",
        reply_markup=create_main_menu_keyboard()
    )


@dp.callback_query(F.data.startswith("text_pos:"))
async def cb_text_position(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработка выбора позиции текста"""
    position = callback.data.split(":")[1]
    # Сохраняем user_id в state
    await state.update_data(
        text_position=position,
        user_id=callback.from_user.id  # Добавляем user_id
    )
    await callback.answer()

    await generate_final_sticker(callback.message, state, bot)


@dp.callback_query(F.data.startswith("add_text_to:"))
async def cb_add_text_to_existing(callback: CallbackQuery, state: FSMContext):
    """Добавляет текст к существующему стикеру"""
    sticker_id = int(callback.data.split(":")[1])
    await state.update_data(adding_text_to_sticker=sticker_id)

    await callback.message.answer(
        "✏️ <b>Введите текст для добавления на стикер</b>\n\n"
        "Максимум 20 символов",
        parse_mode=ParseMode.HTML
    )

    await state.set_state(StickerGeneration.waiting_for_text)
    await callback.answer()


@dp.callback_query(F.data.startswith("change_text:"))
async def cb_change_text(callback: CallbackQuery, state: FSMContext):
    """Позволяет изменить текст на существующем стикере"""
    await callback.answer()

    sticker_id = int(callback.data.split(":")[1])
    await state.update_data(editing_sticker_id=sticker_id)

    await callback.message.answer(
        "✏️ <b>Введите новый текст для стикера</b>\n\n"
        "Или отправьте <code>-</code> чтобы убрать текст",
        parse_mode=ParseMode.HTML
    )

    await state.set_state(StickerGeneration.waiting_for_text)


@dp.message(StateFilter(StickerGeneration.waiting_for_text))
async def process_sticker_text(message: Message, state: FSMContext, bot: Bot):
    """Обрабатывает введенный текст и генерирует стикер"""
    # Ограничиваем длину текста
    text = message.text.strip()[:20]

    # Проверяем, это добавление к существующему стикеру или новый
    data = await state.get_data()

    if data.get('adding_text_to_sticker'):
        # Добавляем текст к существующему стикеру
        sticker_id = data['adding_text_to_sticker']
        await add_text_to_existing_sticker(message, state, bot, sticker_id, text)
    elif data.get('editing_sticker_id'):
        # Изменяем текст на существующем стикере
        sticker_id = data['editing_sticker_id']
        if text == "-":
            text = None
        await edit_sticker_text(message, state, bot, sticker_id, text)
    else:
        # Обычная генерация с текстом
        # Сохраняем текст
        await state.update_data(sticker_text=text)

        # Спрашиваем позицию текста
        await message.answer(
            f"📍 <b>Где разместить текст?</b>\n\n"
            f"Текст: <b>{text}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=create_text_position_keyboard()
        )


async def generate_final_sticker(message: Message, state: FSMContext, bot: Bot):
    """Генерирует стикер с учетом всех параметров"""
    processing_msg = None  # Initialize processing_msg to None

    try:
        # Получаем все данные
        data = await state.get_data()
        prompt = data.get('prompt')
        style = data.get('style', 'realistic')  # Оставляем реалистичный стиль
        sticker_text = data.get('sticker_text')
        text_position = data.get('text_position', 'bottom')

        user_id = data.get('user_id')
        if not user_id:
            user_id = message.from_user.id
            await state.update_data(user_id=user_id) # Save it for future steps

        logger.info(f"Generating sticker for user_id: {user_id}")

        # Отправляем сообщение о генерации
        processing_msg = await message.answer(
            "🎨 <b>Генерирую стикер...</b>\n\n"
            f"<b>Описание:</b> <i>{prompt}</i>\n"
            f"<b>Стиль:</b> {STYLE_NAMES.get(style, style)}\n"
            f"<b>Текст:</b> {sticker_text or 'Без текста'}\n\n"
            "⏳ Это займет несколько секунд...",
            parse_mode=ParseMode.HTML
        )

        # Генерируем стикер
        image_data, metadata = await image_service.generate_sticker_with_validation(
            prompt=prompt,
            style=style
        )

        if not image_data:
            await processing_msg.edit_text(
                "❌ Не удалось сгенерировать стикер. Попробуйте еще раз.",
                reply_markup=create_main_menu_keyboard()
            )
            await state.clear()
            return

        # Обрабатываем стикер (масштабирование + добавление текста)
        processed_bytes = await process_sticker(
            image_data,
            target_size=(512, 512),
            enhance_quality=True,
            add_text=sticker_text,
            text_position=text_position if sticker_text else None
        )

        # Сохраняем файл
        filename = f"sticker_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        file_path = STORAGE_DIR / filename

        with open(file_path, "wb") as f:
            f.write(processed_bytes)

        # Обновляем метаданные
        metadata['text'] = sticker_text
        metadata['text_position'] = text_position if sticker_text else None

        # Сохраняем в БД
        sticker_id = await db_manager.save_sticker(
            user_id=user_id,
            prompt=prompt,
            style=style,
            background="auto",
            file_path=str(file_path),
            metadata=json.dumps(metadata)
        )

        if not sticker_id:
            logger.error("Failed to save sticker to database")
            await processing_msg.edit_text(
                "❌ Ошибка сохранения стикера",
                reply_markup=create_main_menu_keyboard()
            )
            await state.clear()
            return

        logger.info(f"Sticker saved with ID: {sticker_id} for user: {user_id}")

        # Обновляем статистику
        await db_manager.increment_user_stat(user_id, 'stickers_created')

        # Удаляем сообщение о прогрессе
        try:
            await processing_msg.delete()
            processing_msg = None # Mark as deleted
        except Exception as e:
            logger.debug(f"Could not delete progress message: {e}")

        # Небольшая задержка чтобы БД успела сохранить
        await asyncio.sleep(0.1)

        # Проверяем что стикер действительно в БД
        verify = await db_manager.fetchone(
            "SELECT id FROM stickers WHERE id = ?",
            (sticker_id,)
        )

        if not verify:
            logger.error(f"Sticker {sticker_id} not found after save!")
            await message.answer("❌ Ошибка сохранения стикера")
            await state.clear()
            return

        # Отправляем стикер БЕЗ caption
        logger.info(f"Sending sticker with ID: {sticker_id}")
        await message.answer_sticker(
            sticker=FSInputFile(file_path)
        )

        # Сначала отправляем клавиатуру действий в отдельном сообщении
        await message.answer(
            "🎨 <b>Стикер создан!</b>\n\nВыберите действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=create_sticker_actions_keyboard(sticker_id, has_text=bool(sticker_text))
        )

        # НОВОЕ: Автоматическое добавление в пак
        # Отправляем отдельное сообщение о процессе
        auto_add_msg = await message.answer(
            "🔄 <b>Добавляю в ваш стикерпак...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            # Читаем файл стикера
            with open(file_path, 'rb') as f:
                sticker_bytes = f.read()

            # Получаем эмодзи для стикера
            emoji = get_emoji_for_prompt(prompt)

            # Добавляем в пак
            success, pack_name, error = await sticker_manager.get_or_create_user_pack(
                bot=bot,
                user_id=user_id,
                user_name=message.from_user.first_name or "User", # Use message.from_user
                sticker_bytes=sticker_bytes,
                emoji=emoji
            )

            try:
                await auto_add_msg.delete()
                auto_add_msg = None # Mark as deleted
            except Exception:
                pass

            if success:
                await db_manager.save_user_pack(user_id, pack_name)
                await db_manager.add_sticker_to_pack(
                    user_id,
                    pack_name,
                    sticker_id
                )

                pack_link = sticker_manager.get_pack_link(pack_name)

                builder = InlineKeyboardBuilder()
                builder.button(text="📦 Открыть стикерпак", url=pack_link)
                builder.button(text="🔄 Обновить пак", callback_data=f"refresh_pack:{pack_name}")
                builder.button(text="➕ Новый стикер", callback_data="new_sticker")
                builder.button(text="🖼 Мои стикеры", callback_data="my_stickers")
                builder.adjust(1, 1, 2)

                # Получаем количество стикеров в паке
                pack_count = await get_pack_sticker_count(bot, pack_name)

                await message.answer(
                    "✅ <b>Стикер добавлен в ваш пак!</b>\n\n"
                    f"📦 Пак: <code>{pack_name}</code>\n"
                    f"📊 Стикеров в паке: {pack_count}\n\n"
                    "💡 <i>Если стикер не виден - нажмите 'Обновить пак' или переоткройте его в Telegram</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup()
                )

            else:
                # Улучшенная обработка ошибок
                if "STICKERSET_INVALID" in str(error):
                    # Очищаем недействительные паки
                    await sticker_manager.cleanup_invalid_packs(bot, user_id)

                    # Пробуем еще раз после очистки
                    processing_msg2 = await message.answer(
                        "🔄 <b>Обнаружен недействительный пак. Создаю новый...</b>",
                        parse_mode=ParseMode.HTML
                    )

                    success2, pack_name2, error2 = await sticker_manager.get_or_create_user_pack(
                        bot=bot,
                        user_id=user_id,
                        user_name=message.from_user.first_name or "User", # Use message.from_user
                        sticker_bytes=sticker_bytes,
                        emoji=emoji
                    )

                    try:
                        await processing_msg2.delete()
                    except Exception:
                        pass

                    if success2:
                        await db_manager.save_user_pack(user_id, pack_name2)
                        await db_manager.add_sticker_to_pack(user_id, pack_name2, sticker_id)

                        pack_link = sticker_manager.get_pack_link(pack_name2)

                        builder = InlineKeyboardBuilder()
                        builder.button(text="📦 Открыть стикерпак", url=pack_link)
                        builder.button(text="🔄 Обновить пак", callback_data=f"refresh_pack:{pack_name2}")
                        builder.button(text="➕ Новый стикер", callback_data="new_sticker")
                        builder.adjust(1, 1, 1)

                        await message.answer(
                            "✅ <b>Создан новый пак и стикер добавлен!</b>\n\n"
                            f"📦 Пак: <code>{pack_name2}</code>\n\n"
                            "💡 <i>Старый пак был недействителен и удален из БД</i>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=builder.as_markup()
                        )

                    else:
                        await message.answer(
                            f"❌ <b>Ошибка создания нового пака:</b>\n{escape_html(error2 or 'Неизвестная ошибка')}",
                            parse_mode=ParseMode.HTML,
                            reply_markup=create_main_menu_keyboard()
                        )

                elif error == "Достигнут лимит стикерпаков (10)":
                    error_text = (
                        "❌ <b>Достигнут лимит паков</b>\n\n"
                        f"У вас уже {MAX_PACKS_PER_USER} стикерпаков (максимум).\n"
                        "Удалите старые паки через @Stickers"
                    )
                    await message.answer(
                        error_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=create_main_menu_keyboard()
                    )

                elif error == "pack_full":
                    error_text = (
                        "❌ <b>Пак переполнен</b>\n\n"
                        f"В текущем паке уже {MAX_STICKERS_PER_PACK} стикеров (максимум).\n"
                        "Создастся новый пак при следующей попытке."
                    )
                    await message.answer(
                        error_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=create_main_menu_keyboard()
                    )

                else:
                    error_text = f"❌ <b>Ошибка:</b> {escape_html(error or 'Не удалось добавить стикер')}"
                    await message.answer(
                        error_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=create_main_menu_keyboard()
                    )

        except Exception as e:
            logger.error(f"Error auto-adding to pack: {e}")
            if auto_add_msg: # Check if it exists before trying to delete
                try:
                    await auto_add_msg.delete()
                except Exception:
                    pass

            await message.answer(
                "⚠️ Не удалось автоматически добавить в пак.\n"
                "Используйте кнопку '📦 В стикерпак' для ручного добавления.",
                parse_mode=ParseMode.HTML
            )
        finally:
            # Убираем стикер из обработки
            processing_stickers.discard(sticker_id)

    except Exception as e:
        logger.error(f"Error generating sticker: {e}")
        if processing_msg: # Check if it exists before trying to delete
            try:
                await processing_msg.delete()
            except Exception:
                pass
        # Информируем пользователя
        await message.answer(
            "❌ Произошла ошибка при создании стикера. Попробуйте еще раз.",
            reply_markup=create_main_menu_keyboard()
        )
    finally:
        # Очищаем состояние
        await state.clear()


async def add_text_to_existing_sticker(message: Message, state: FSMContext, bot: Bot, sticker_id: int, text: str):
    """Добавляет текст к существующему стикеру"""
    try:
        # Получаем информацию о стикере
        sticker_data = await db_manager.fetchone(
            "SELECT file_path, prompt, style, metadata FROM stickers WHERE id = ? AND user_id = ?",
            (sticker_id, message.from_user.id)
        )

        if not sticker_data:
            await message.answer("Стикер не найден")
            await state.clear()
            return

        progress_msg = await message.answer("✏️ Добавляю текст...")

        # Читаем оригинальный стикер
        with open(sticker_data['file_path'], 'rb') as f:
            original_bytes = f.read()

        # Добавляем текст
        processed_bytes = await process_sticker(
            original_bytes,
            add_text=text,
            text_position="bottom" # Default to bottom for existing stickers
        )

        # Сохраняем новую версию
        filename = f"sticker_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_text.png"
        file_path = STORAGE_DIR / filename

        with open(file_path, "wb") as f:
            f.write(processed_bytes)

        # Обновляем метаданные
        metadata = json.loads(sticker_data['metadata'] or '{}')
        metadata['text'] = text
        metadata['text_added_after'] = True
        # Keep original prompt and style for the new sticker entry if needed
        metadata['prompt'] = sticker_data['prompt']
        metadata['style'] = sticker_data['style']


        # Сохраняем новый стикер в БД
        new_sticker_id = await db_manager.save_sticker(
            user_id=message.from_user.id,
            prompt=sticker_data['prompt'], # Use original prompt
            style=sticker_data['style'],   # Use original style
            background="auto",
            file_path=str(file_path),
            metadata=json.dumps(metadata)
        )
        if not new_sticker_id:
            logger.error("Failed to save new sticker with text to database")
            await progress_msg.edit_text("❌ Ошибка сохранения стикера")
            await state.clear()
            return

        await progress_msg.delete()

        # Отправляем обновленный стикер
        await message.answer_sticker(
            sticker=FSInputFile(file_path),
            reply_markup=create_sticker_actions_keyboard(new_sticker_id, has_text=True)
        )
        await message.answer(
            "✅ <b>Текст добавлен!</b>\n\nВыберите действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=create_sticker_actions_keyboard(new_sticker_id, has_text=True)
        )


        await state.clear()

    except Exception as e:
        logger.error(f"Error adding text to sticker: {e}")
        await message.answer("❌ Ошибка при добавлении текста")
        await state.clear()


async def edit_sticker_text(message: Message, state: FSMContext, bot: Bot, sticker_id: int, new_text: Optional[str]):
    """Изменяет текст на существующем стикере"""
    try:
        # Получаем информацию о стикере
        sticker_data = await db_manager.fetchone(
            "SELECT file_path, prompt, style, metadata FROM stickers WHERE id = ? AND user_id = ?",
            (sticker_id, message.from_user.id)
        )

        if not sticker_data:
            await message.answer("Стикер не найден")
            await state.clear()
            return

        progress_msg = await message.answer("✏️ Изменяю текст...")

        # Читаем оригинальный стикер
        # For editing, we might need to regenerate from the *original* image data
        # without any text, then apply the new text.
        # This requires storing the original image generation data more persistently
        # or regenerating the base image.
        # For simplicity and given the current structure, we'll re-process the
        # latest sticker image, which might lead to text over text if not careful.
        # A better approach would be to store the base image data or regenerate it.
        # Assuming `process_sticker` can handle removing text if new_text is None
        # or overwriting it.

        original_image_path = sticker_data['file_path']
        with open(original_image_path, 'rb') as f:
            base_bytes = f.read()

        # Get existing metadata to preserve style and prompt for new sticker entry
        metadata = json.loads(sticker_data['metadata'] or '{}')
        original_prompt = sticker_data.get('prompt', metadata.get('prompt', ''))
        original_style = sticker_data.get('style', metadata.get('style', 'realistic'))
        # Determine text position, default to bottom if not in metadata
        text_position = metadata.get('text_position', 'bottom') if new_text else None

        processed_bytes = await process_sticker(
            base_bytes,
            add_text=new_text,
            text_position=text_position if new_text else None
        )

        # Save new version
        filename = f"sticker_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_edited.png"
        file_path = STORAGE_DIR / filename

        with open(file_path, "wb") as f:
            f.write(processed_bytes)

        # Update metadata for the new sticker entry
        metadata['text'] = new_text
        metadata['text_position'] = text_position if new_text else None
        metadata['text_edited_after'] = True


        # Save new sticker in DB
        new_sticker_id = await db_manager.save_sticker(
            user_id=message.from_user.id,
            prompt=original_prompt,
            style=original_style,
            background="auto",
            file_path=str(file_path),
            metadata=json.dumps(metadata)
        )
        if not new_sticker_id:
            logger.error("Failed to save edited sticker to database")
            await progress_msg.edit_text("❌ Ошибка сохранения стикера")
            await state.clear()
            return

        await progress_msg.delete()

        # Send updated sticker
        await message.answer_sticker(
            sticker=FSInputFile(file_path),
            reply_markup=create_sticker_actions_keyboard(new_sticker_id, has_text=bool(new_text))
        )
        await message.answer(
            f"✅ <b>Текст {'изменен' if new_text else 'удален'}!</b>\n\nВыберите действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=create_sticker_actions_keyboard(new_sticker_id, has_text=bool(new_text))
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Error editing sticker text: {e}")
        await message.answer("❌ Ошибка при изменении текста")
        await state.clear()


@dp.callback_query(F.data == "show_examples")
async def cb_show_examples(callback: CallbackQuery):
    """Показывает примеры запросов"""
    examples_text = """📝 <b>Примеры запросов для стикеров:</b>

<b>Эмоции:</b>
• "Радостный котик с подарком"
• "Грустный пёсик под дождём"
• "Удивлённая сова в очках"

<b>Действия:</b>
• "Программист за компьютером"
• "Девушка пьёт кофе"
• "Мальчик играет в футбол"

<b>Праздники:</b>
• "С днём рождения с тортом"
• "С новым годом и ёлкой"
• "Валентинка с сердечками"

<b>Абстрактные:</b>
• "Космический кот в скафандре"
• "Единорог на радуге"
• "Робот с цветами"

<b>✏️ Примеры с текстом:</b>
• Кот + "MEOW"
• Сердце + "LOVE"
• Торт + "YAM!"

💡 <i>Чем детальнее описание, тем интереснее результат!</i>"""

    builder = InlineKeyboardBuilder()
    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        examples_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@dp.callback_query(F.data == "show_styles")
async def cb_show_styles(callback: CallbackQuery):
    """Показывает доступные стили"""
    styles_text = """🎨 <b>Доступные стили стикеров:</b>

🌟 <b>Cartoon</b> - Мультяшный стиль
🎭 <b>Anime</b> - Аниме стиль
📸 <b>Realistic</b> - Реалистичный
⚡ <b>Minimalist</b> - Минималистичный
🎮 <b>Pixel</b> - Пиксель арт
💞 <b>Cute</b> - Милый стиль

💡 <b>Умная генерация фона:</b>
• Просто "кот" → кот без фона
• "Кот в космосе" → кот с космическим фоном
• AI сам определяет нужен ли фон!

✏️ <b>Добавление текста:</b>
• После выбора стиля можно добавить текст
• Текст накладывается поверх готового стикера
• Можно выбрать позицию: сверху, снизу, по центру

✨ <i>Стикеры создаются в стиле Telegram!</i>"""

    builder = InlineKeyboardBuilder()
    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        styles_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    try:
        await state.clear()

        await callback.message.answer(
            "🎨 <b>Главное меню</b>\n\n"
            "Выберите действие или отправьте мне описание для стикера:",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )

        try:
            # Используем chat_id вместо message.chat.id, так как message может быть изменен
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
        except Exception as e:
            logger.warning(f"Failed to delete message in cb_back_to_menu: {e}")

        await callback.answer("Готов создать новый стикер! 🎨")

    except Exception as e:
        logger.error(f"Error in cb_back_to_menu: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@dp.callback_query(F.data == "new_sticker")
async def cb_new_sticker(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки '➕ Новый стикер'"""
    await cb_create_sticker(callback, state)


@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки '🏠 Меню'"""
    await cb_back_to_menu(callback, state)


@dp.callback_query(F.data == "tips")
async def cb_tips(callback: CallbackQuery):
    """Показать советы через callback"""
    await callback.answer()
    await cmd_tips(callback.message)


@dp.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    """Показать помощь через callback"""
    await callback.answer()
    await cmd_help(callback.message)


@dp.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):
    """Показать статистику через callback"""
    await callback.answer()

    class FakeMessage:
        def __init__(self, from_user_obj):
            self.from_user = from_user_obj

    fake_message = FakeMessage(callback.from_user)
    await cmd_stats(fake_message)


@dp.callback_query(F.data == "my_stickers")
async def cb_my_stickers(callback: CallbackQuery):
    """Показать стикеры пользователя через callback"""
    await callback.answer()
    await show_user_stickers(callback.from_user.id, callback.message)


@dp.callback_query(F.data == "my_packs")
async def cb_my_packs(callback: CallbackQuery):
    """Показать стикерпаки через callback"""
    await callback.answer()

    packs = await db_manager.get_user_packs(callback.from_user.id)

    if not packs:
        await callback.message.edit_text(
            "📦 <b>У вас пока нет стикерпаков</b>\n\n"
            "Создайте стикер и добавьте его в пак!",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    text = "📦 <b>Ваши стикерпаки:</b>\n\n"

    builder = InlineKeyboardBuilder()

    for i, pack in enumerate(packs, 1):
        pack_link = sticker_manager.get_pack_link(pack['pack_name'])
        text += f"{i}. Стикерпак №{i} - {pack['stickers_count']} стикеров\n"
        builder.button(
            text=f"📦 Открыть пак №{i}",
            url=pack_link
        )

    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data.startswith("add_to_pack:"))
async def cb_add_to_sticker_pack(callback: CallbackQuery, bot: Bot):
    """Добавляет стикер в стикерпак пользователя с защитой от дублирования"""
    processing_msg = None  # Initialize processing_msg here
    try:
        sticker_id = int(callback.data.split(":")[1])
        user_id = callback.from_user.id

        # Защита от дублирования
        if sticker_id in processing_stickers:
            await callback.answer("⏳ Стикер уже обрабатывается...", show_alert=True)
            return

        processing_stickers.add(sticker_id)

        # Добавляем логирование для отладки
        logger.info(f"Adding sticker to pack - ID: {sticker_id}, User: {user_id}")

        # Проверяем, не добавлен ли уже стикер в пак
        existing = await db_manager.fetchone(
            """
            SELECT sp.pack_name
            FROM sticker_pack_items spi
            JOIN user_sticker_packs sp ON spi.pack_id = sp.id
            WHERE spi.sticker_id = ? AND sp.user_id = ?
            """,
            (sticker_id, user_id)
        )

        if existing:
            processing_stickers.discard(sticker_id)
            await callback.answer(
                f"Стикер уже в паке {existing['pack_name']}",
                show_alert=True
            )
            return

        sticker_data = await db_manager.fetchone(
            "SELECT file_path, prompt FROM stickers WHERE id = ? AND user_id = ?",
            (sticker_id, user_id)
        )

        if not sticker_data:
            processing_stickers.discard(sticker_id)
            await callback.answer("Стикер не найден", show_alert=True)
            return

        # Проверяем существование файла
        if not os.path.exists(sticker_data['file_path']):
            processing_stickers.discard(sticker_id)
            logger.error(f"Sticker file not found: {sticker_data['file_path']}")
            await callback.answer("Файл стикера не найден", show_alert=True)
            return

        processing_msg = await callback.message.answer(
            "📦 <b>Добавляю в стикерпак...</b>\n\n"
            "<i>Это может занять несколько секунд</i>",
            parse_mode=ParseMode.HTML
        )

        with open(sticker_data['file_path'], 'rb') as f:
            sticker_bytes = f.read()

        emoji = get_emoji_for_prompt(sticker_data['prompt'])

        success, pack_name, error = await sticker_manager.get_or_create_user_pack(
            bot=bot,
            user_id=user_id,
            user_name=callback.from_user.first_name or "User",
            sticker_bytes=sticker_bytes,
            emoji=emoji
        )

        try:
            if processing_msg: # Check if it exists before trying to delete
                await processing_msg.delete()
                processing_msg = None # Mark as deleted
        except Exception:
            pass

        if success:
            await db_manager.save_user_pack(user_id, pack_name)
            await db_manager.add_sticker_to_pack(
                user_id,
                pack_name,
                sticker_id
            )

            pack_link = sticker_manager.get_pack_link(pack_name)

            builder = InlineKeyboardBuilder()
            builder.button(text="📦 Открыть стикерпак", url=pack_link)
            builder.button(text="🔄 Обновить пак", callback_data=f"refresh_pack:{pack_name}")
            builder.button(text="➕ Новый стикер", callback_data="new_sticker")
            builder.button(text="🖼 Мои стикеры", callback_data="my_stickers")
            builder.adjust(1, 1, 2)

            # Получаем количество стикеров в паке
            pack_count = await get_pack_sticker_count(bot, pack_name)

            await callback.message.answer(
                "✅ <b>Стикер добавлен в ваш пак!</b>\n\n"
                f"📦 Пак: <code>{pack_name}</code>\n"
                f"📊 Стикеров в паке: {pack_count}\n\n"
                "💡 <i>Если стикер не виден - нажмите 'Обновить пак' или переоткройте его в Telegram</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup()
            )

            await callback.answer("✅ Стикер добавлен в пак!")

        else:
            # Улучшенная обработка ошибок
            if "STICKERSET_INVALID" in str(error):
                # Очищаем недействительные паки
                await sticker_manager.cleanup_invalid_packs(bot, user_id)

                # Пробуем еще раз после очистки
                processing_msg2 = await callback.message.answer(
                    "🔄 <b>Обнаружен недействительный пак. Создаю новый...</b>",
                    parse_mode=ParseMode.HTML
                )

                success2, pack_name2, error2 = await sticker_manager.get_or_create_user_pack(
                    bot=bot,
                    user_id=user_id,
                    user_name=callback.from_user.first_name or "User",
                    sticker_bytes=sticker_bytes,
                    emoji=emoji
                )

                try:
                    await processing_msg2.delete()
                except Exception:
                    pass

                if success2:
                    await db_manager.save_user_pack(user_id, pack_name2)
                    await db_manager.add_sticker_to_pack(user_id, pack_name2, sticker_id)

                    pack_link = sticker_manager.get_pack_link(pack_name2)

                    builder = InlineKeyboardBuilder()
                    builder.button(text="📦 Открыть стикерпак", url=pack_link)
                    builder.button(text="🔄 Обновить пак", callback_data=f"refresh_pack:{pack_name2}")
                    builder.button(text="➕ Новый стикер", callback_data="new_sticker")
                    builder.adjust(1, 1, 1)

                    await callback.message.answer(
                        "✅ <b>Создан новый пак и стикер добавлен!</b>\n\n"
                        f"📦 Пак: <code>{pack_name2}</code>\n\n"
                        "💡 <i>Старый пак был недействителен и удален из БД</i>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=builder.as_markup()
                    )

                    await callback.answer("✅ Стикер добавлен в новый пак!")
                else:
                    await callback.message.answer(
                        f"❌ <b>Ошибка создания нового пака:</b>\n{escape_html(error2 or 'Неизвестная ошибка')}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=create_main_menu_keyboard()
                    )
                    await callback.answer("Не удалось создать новый пак", show_alert=True)

            elif error == "Достигнут лимит стикерпаков (10)":
                error_text = (
                    "❌ <b>Достигнут лимит паков</b>\n\n"
                    f"У вас уже {MAX_PACKS_PER_USER} стикерпаков (максимум).\n"
                    "Удалите старые паки через @Stickers"
                )
                await callback.message.answer(
                    error_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=create_main_menu_keyboard()
                )
                await callback.answer("Достигнут лимит паков", show_alert=True)

            elif error == "pack_full":
                error_text = (
                    "❌ <b>Пак переполнен</b>\n\n"
                    f"В текущем паке уже {MAX_STICKERS_PER_PACK} стикеров (максимум).\n"
                    "Создастся новый пак при следующей попытке."
                )
                await callback.message.answer(
                    error_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=create_main_menu_keyboard()
                )
                await callback.answer("Пак переполнен", show_alert=True)

            else:
                error_text = f"❌ <b>Ошибка:</b> {escape_html(error or 'Не удалось добавить стикер')}"
                await callback.message.answer(
                    error_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=create_main_menu_keyboard()
                )
                await callback.answer("Не удалось добавить стикер", show_alert=True)

    except Exception as e:
        logger.error(f"Error in add_to_sticker_pack: {e}")
        if processing_msg: # Check if it exists before trying to delete
            try:
                await processing_msg.delete()
            except Exception:
                pass
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        # Убираем стикер из обработки
        if sticker_id in processing_stickers: # Ensure sticker_id exists before attempting to discard
            processing_stickers.discard(sticker_id)