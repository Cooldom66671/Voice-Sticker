"""
Обработчики команд и сообщений для бота VoiceSticker
Версия с улучшенной точностью, системой обратной связи и навигацией
"""
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
import json

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
    MESSAGES, AVAILABLE_STYLES, BACKGROUND_STYLES,
    STORAGE_DIR, ADMIN_IDS, RATE_LIMIT_MESSAGES_PER_MINUTE
)
from db_manager import db_manager
from image_generation_service import image_service
from stt_service import stt_service
from sticker_utils import process_sticker


# Состояния FSM
class StickerGeneration(StatesGroup):
    """Состояния для процесса генерации стикера"""
    waiting_for_prompt = State()  # Ожидание текста/голоса
    waiting_for_style = State()  # Выбор стиля
    waiting_for_background = State()  # Выбор фона
    processing = State()  # Обработка
    waiting_for_feedback = State()  # Ожидание оценки
    waiting_for_feedback_comment = State()  # Ожидание комментария


# Инициализация диспетчера
dp = Dispatcher()


# Хранилище для временных данных генерации
generation_cache: Dict[int, Dict] = {}


# Вспомогательные функции
def create_style_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора стиля"""
    buttons = []
    styles = [
        ("🎨 Cartoon", "cartoon"),
        ("🎭 Anime", "anime"),
        ("📸 Realistic", "realistic"),
        ("⚡ Минимализм", "minimalist"),
        ("🎮 Пиксель арт", "pixel"),
        ("🖌️ Акварель", "watercolor"),
        ("✏️ Набросок", "sketch"),
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


def create_background_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора фона"""
    buttons = []
    backgrounds = [
        ("🪟 Прозрачный", "transparent"),
        ("⬜ Белый", "white"),
        ("🌈 Градиент", "gradient"),
        ("⭕ Круглый", "circle"),
        ("🔲 Закругленный", "rounded"),
    ]

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(backgrounds), 2):
        row = []
        for j in range(2):
            if i + j < len(backgrounds):
                name, value = backgrounds[i + j]
                row.append(InlineKeyboardButton(
                    text=name,
                    callback_data=f"bg:{value}"
                ))
        buttons.append(row)

    # Добавляем кнопку "Назад"
    buttons.append([InlineKeyboardButton(text="◀️ Назад к стилям", callback_data="back_to_styles")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_feedback_keyboard(sticker_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для оценки стикера с кнопкой создания нового"""
    builder = InlineKeyboardBuilder()

    # Кнопки оценки в одну строку
    for i in range(1, 6):
        builder.button(text=f"{'⭐' * i}", callback_data=f"rate:{sticker_id}:{i}")

    # Размещаем кнопки оценки в одну строку
    builder.adjust(5)

    # Функциональные кнопки
    builder.button(text="🔄 Создать заново", callback_data=f"retry:{sticker_id}")
    builder.button(text="💬 Комментарий", callback_data=f"comment:{sticker_id}")
    builder.button(text="➕ Новый стикер", callback_data="back_to_menu")

    # Размещаем: 5 в первой строке, 3 во второй
    builder.adjust(5, 3)

    return builder.as_markup()


def create_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает главное меню"""
    builder = InlineKeyboardBuilder()

    # Главная кнопка
    builder.button(text="🎨 Создать стикер", callback_data="create_sticker")
    builder.adjust(1)  # Одна в строке

    # Функциональные кнопки
    builder.button(text="📊 Статистика", callback_data="stats")
    builder.button(text="🖼 Мои стикеры", callback_data="my_stickers")
    builder.button(text="📝 Примеры", callback_data="show_examples")
    builder.button(text="🎨 Стили", callback_data="show_styles")
    builder.button(text="❓ Помощь", callback_data="help")
    builder.button(text="💡 Советы", callback_data="tips")

    # Размещаем остальные кнопки по 2 в строке
    builder.adjust(1, 2, 2, 2)

    return builder.as_markup()


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    user = message.from_user

    # Сохраняем пользователя в БД
    await db_manager.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code
    )

    # Очищаем состояние
    await state.clear()

    # Логируем действие
    log_user_action(user.id, "start_command")

    # Обновленное приветствие
    welcome_text = f"""👋 <b>Привет, {user.first_name}!</b>

🎨 Я - VoiceSticker Bot! Создаю уникальные стикеры из ваших идей!

<b>🎯 Что нового:</b>
• Улучшенная точность генерации
• Система оценок для обучения
• Удобная навигация

<b>🚀 Как начать:</b>
Просто отправьте мне описание желаемого стикера текстом или голосом!

Выберите действие:"""

    # Отправляем приветствие с главным меню
    await message.answer(
        welcome_text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    log_user_action(message.from_user.id, "help_command")

    help_text = """📚 <b>Помощь VoiceSticker Bot</b>

<b>🎯 Как создать точный стикер:</b>
• Опишите главный объект четко
• Добавьте детали: цвет, эмоции, действия
• Используйте простые формулировки

<b>✅ Хорошие примеры:</b>
• "Красный дракон дышит огнём"
• "Грустный котик под дождём"
• "Веселый программист с кофе"

<b>❌ Избегайте:</b>
• Слишком общих описаний
• Множества объектов
• Сложных сцен

<b>🎨 Стили:</b>
• <b>Cartoon</b> - яркий мультяшный
• <b>Anime</b> - японская анимация
• <b>Realistic</b> - фотореалистичный
• <b>Minimalist</b> - простой и чистый
• <b>Pixel</b> - пиксельная графика
• <b>Watercolor</b> - акварель
• <b>Sketch</b> - набросок

<b>⭐ Оценивайте результаты!</b>
Это помогает мне создавать более точные стикеры."""

    await message.answer(
        help_text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Обработчик команды /stats с расширенной статистикой"""
    user_id = message.from_user.id
    log_user_action(user_id, "stats_command")

    # Получаем статистику
    stats = await db_manager.get_user_stats(user_id)

    if not stats:
        await message.answer("❌ Ошибка получения статистики")
        return

    # Получаем дополнительную статистику по оценкам
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

    # Форматируем дату
    registered = datetime.fromisoformat(stats['registered_at']).strftime("%d.%m.%Y")

    # Безопасное вычисление процентов
    total_rated = ratings_stats['total_rated'] or 0
    good_ratings = ratings_stats['good_ratings'] or 0
    avg_rating = ratings_stats['avg_rating'] or 0.0
    success_percent = (good_ratings / total_rated * 100) if total_rated > 0 else 0

    text = f"""📊 <b>Ваша статистика:</b>

👤 Имя: {stats['first_name'] or 'Не указано'}
📅 Дата регистрации: {registered}

🎨 Создано стикеров: {stats['total_stickers']}
🎤 Голосовых сообщений: {stats['total_voice_messages']}
💬 Текстовых сообщений: {stats['total_text_messages']}

<b>⭐ Качество генерации:</b>
• Средняя оценка: {avg_rating:.1f}/5.0
• Успешных генераций: {good_ratings}/{total_rated} ({success_percent:.0f}%)
"""

    await message.answer(
        text,
        reply_markup=create_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("mystickers"))
async def cmd_mystickers(message: Message):
    """Обработчик команды /mystickers"""
    await show_user_stickers(message.from_user.id, message)


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

    # Очищаем предыдущее состояние
    await state.clear()

    await state.set_state(StickerGeneration.waiting_for_prompt)

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


@dp.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    try:
        # Очищаем состояние
        await state.clear()

        # Отправляем главное меню
        await callback.message.answer(
            "🎨 <b>Главное меню</b>\n\n"
            "Выберите действие или отправьте мне описание для стикера:",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )

        # Пытаемся удалить предыдущее сообщение
        try:
            await callback.message.delete()
        except:
            pass

        await callback.answer("Готов создать новый стикер! 🎨")

    except Exception as e:
        logger.error(f"Error in cb_back_to_menu: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@dp.callback_query(F.data == "back_to_styles")
async def cb_back_to_styles(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору стилей"""
    await callback.answer()

    # Возвращаемся к состоянию выбора стиля
    await state.set_state(StickerGeneration.waiting_for_style)

    # Получаем сохраненный промпт
    data = await state.get_data()
    prompt = data.get("prompt", "")

    await callback.message.edit_text(
        f"📝 Ваш запрос: <i>{prompt}</i>\n\n"
        f"🎨 <b>Выберите стиль стикера:</b>",
        reply_markup=create_style_keyboard(),
        parse_mode=ParseMode.HTML
    )


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
🖌️ <b>Watercolor</b> - Акварельный
✏️ <b>Sketch</b> - Карандашный набросок

🖼 <b>Доступные фоны:</b>
• Прозрачный
• Белый
• Градиент
• Круглый
• Закругленный

💡 <i>Экспериментируйте с разными комбинациями!</i>"""

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
    # Создаем фейковое сообщение для передачи user_id
    class FakeMessage:
        def __init__(self, from_user):
            self.from_user = from_user

    fake_message = FakeMessage(callback.from_user)
    await cmd_stats(fake_message)


@dp.callback_query(F.data == "my_stickers")
async def cb_my_stickers(callback: CallbackQuery):
    """Показать стикеры пользователя через callback"""
    await callback.answer()
    await show_user_stickers(callback.from_user.id, callback.message)


# Обработчики выбора стиля
@dp.callback_query(F.data.startswith("style:"))
async def cb_select_style(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора стиля"""
    await callback.answer()

    style = callback.data.split(":")[1]
    await state.update_data(style=style)

    # Переходим к выбору фона
    await state.set_state(StickerGeneration.waiting_for_background)

    # Показываем описание выбранного стиля
    style_descriptions = {
        "cartoon": "яркий и веселый мультяшный стиль",
        "anime": "японская анимация с выразительными глазами",
        "realistic": "фотореалистичное изображение",
        "minimalist": "простой стиль с минимумом деталей",
        "pixel": "ретро пиксельная графика",
        "watercolor": "нежная акварельная живопись",
        "sketch": "карандашный набросок"
    }

    await callback.message.edit_text(
        f"✅ Выбран стиль: <b>{style_descriptions.get(style, style)}</b>\n\n"
        "🖼 <b>Теперь выберите фон для стикера:</b>",
        reply_markup=create_background_keyboard(),
        parse_mode=ParseMode.HTML
    )


# Обработчики выбора фона
@dp.callback_query(F.data.startswith("bg:"))
async def cb_select_background(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработка выбора фона и генерация стикера"""
    await callback.answer()

    background = callback.data.split(":")[1]
    await state.update_data(background=background)

    # Получаем данные из состояния
    data = await state.get_data()
    prompt = data.get("prompt", "")
    style = data.get("style", "default")

    # Переходим в состояние обработки
    await state.set_state(StickerGeneration.processing)

    # Уведомляем о начале генерации с деталями
    await callback.message.edit_text(
        f"⏳ <b>Генерирую ваш стикер...</b>\n\n"
        f"📝 <i>{prompt}</i>\n"
        f"🎨 Стиль: {style}\n"
        f"🖼 Фон: {background}\n\n"
        f"<i>Это займёт 15-20 секунд...</i>",
        parse_mode=ParseMode.HTML
    )

    try:
        # Генерируем изображение с улучшенным сервисом
        image_data, metadata = await image_service.generate_sticker_with_validation(
            prompt,
            style,
            user_id=callback.from_user.id
        )

        if not image_data:
            raise Exception("Не удалось создать изображение")

        # Обрабатываем стикер
        sticker_bytes = await process_sticker(image_data, background)

        # Сохраняем файл
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sticker_{callback.from_user.id}_{timestamp}.png"
        file_path = STORAGE_DIR / filename

        with open(file_path, "wb") as f:
            f.write(sticker_bytes)

        # Сохраняем в БД с метаданными
        sticker_id = await db_manager.save_sticker(
            user_id=callback.from_user.id,
            prompt=prompt,
            style=style,
            background=background,
            file_path=str(file_path),
            file_id=None,  # Заполним после отправки
            metadata=json.dumps(metadata) if metadata else None
        )

        # Сохраняем в кэш для обратной связи
        generation_cache[callback.from_user.id] = {
            'sticker_id': sticker_id,
            'prompt': prompt,
            'style': style,
            'metadata': metadata
        }

        # Удаляем сообщение о генерации
        await callback.message.delete()

        # Отправляем стикер с кнопками оценки
        sticker_file = FSInputFile(file_path)
        sent_message = await bot.send_document(
            chat_id=callback.from_user.id,
            document=sticker_file,
            caption=(
                f"✨ <b>Ваш стикер готов!</b>\n\n"
                f"📝 <i>{prompt}</i>\n"
                f"🎨 Стиль: {style}\n\n"
                f"<b>Оцените результат:</b>\n"
                f"Это поможет мне создавать более точные стикеры!"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=create_feedback_keyboard(sticker_id)
        )

        # Обновляем file_id в БД
        if sent_message.document:
            await db_manager.execute(
                "UPDATE stickers SET file_id = ? WHERE id = ?",
                (sent_message.document.file_id, sticker_id)
            )

        # Переходим в состояние ожидания обратной связи
        await state.set_state(StickerGeneration.waiting_for_feedback)

        # Логируем успех
        log_user_action(callback.from_user.id, "sticker_created", {
            "prompt": prompt,
            "style": style,
            "background": background
        })

    except Exception as e:
        logger.error(f"Ошибка при создании стикера: {e}")
        log_error(e, {"user_id": callback.from_user.id})

        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при генерации</b>\n\n"
            "Попробуйте:\n"
            "• Изменить описание\n"
            "• Выбрать другой стиль\n"
            "• Быть более конкретным\n\n"
            "Или попробуйте позже.",
            reply_markup=create_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )

        # Очищаем состояние при ошибке
        await state.clear()


# Обработчики оценок
@dp.callback_query(F.data.startswith("rate:"))
async def cb_rate_sticker(callback: CallbackQuery, state: FSMContext):
    """Обработка оценки стикера"""
    parts = callback.data.split(":")
    sticker_id = int(parts[1])
    rating = int(parts[2])

    # Сохраняем оценку в БД
    await db_manager.execute(
        "UPDATE stickers SET rating = ? WHERE id = ?",
        (rating, sticker_id)
    )

    # Получаем данные из кэша для обратной связи в сервис
    user_cache = generation_cache.get(callback.from_user.id)
    if user_cache and user_cache['sticker_id'] == sticker_id:
        # Отправляем обратную связь в сервис генерации
        if hasattr(image_service, 'record_feedback'):
            image_service.record_feedback(user_cache['metadata'], rating)

    # Формируем ответ в зависимости от оценки
    rating_responses = {
        5: "🎉 Отлично! Рад, что стикер получился идеальным!",
        4: "😊 Хорошо! Буду стараться делать ещё лучше!",
        3: "🤔 Понял, буду улучшаться!",
        2: "😕 Жаль, что не очень получилось. Попробуйте ещё раз с более детальным описанием.",
        1: "😔 Извините, что не оправдал ожиданий. Давайте попробуем снова?"
    }

    # Создаем клавиатуру в зависимости от оценки
    builder = InlineKeyboardBuilder()

    if rating <= 3:
        builder.button(text="🔄 Попробовать снова", callback_data=f"retry:{sticker_id}")

    builder.button(text="➕ Новый стикер", callback_data="back_to_menu")
    builder.button(text="🖼 Мои стикеры", callback_data="my_stickers")

    # Размещаем кнопки
    if rating <= 3:
        builder.adjust(1, 2)
    else:
        builder.adjust(2)

    # Обновляем сообщение
    await callback.message.edit_caption(
        caption=(
            f"{callback.message.caption}\n\n"
            f"⭐ Ваша оценка: {'⭐' * rating}\n"
            f"{rating_responses[rating]}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )

    # Очищаем состояние после оценки
    await state.clear()

    await callback.answer("Спасибо за оценку!")


# Исправленный обработчик retry
@dp.callback_query(F.data.startswith("retry:"))
async def cb_retry_sticker(callback: CallbackQuery, state: FSMContext):
    """Повторная генерация стикера"""
    try:
        sticker_id = int(callback.data.split(":")[1])

        # Используем правильный метод для получения данных
        sticker_data = await db_manager.fetchone(
            "SELECT prompt, style FROM stickers WHERE id = ?",
            (sticker_id,)
        )

        if sticker_data:
            # Сохраняем данные для повтора
            await state.update_data(
                prompt=sticker_data['prompt'],
                retry_from=sticker_id
            )

            await callback.message.answer(
                f"🔄 <b>Создаю стикер заново</b>\n\n"
                f"Запрос: <i>{sticker_data['prompt']}</i>\n\n"
                f"💡 <b>Совет:</b> Попробуйте выбрать другой стиль или добавить больше деталей!\n\n"
                f"Выберите стиль (предыдущий: {sticker_data['style']}):",
                reply_markup=create_style_keyboard(),
                parse_mode=ParseMode.HTML
            )

            await state.set_state(StickerGeneration.waiting_for_style)
            await callback.answer()
        else:
            await callback.answer("Стикер не найден", show_alert=True)

    except Exception as e:
        logger.error(f"Error in cb_retry_sticker: {e}")
        # Логируем ошибку в БД
        await db_manager.log_error(
            error_type="callback_error",
            error_message=str(e),
            user_id=callback.from_user.id,
            context=f"retry_sticker:{sticker_id}"
        )
        await callback.answer("Произошла ошибка при повторной генерации", show_alert=True)


@dp.callback_query(F.data.startswith("comment:"))
async def cb_add_comment(callback: CallbackQuery, state: FSMContext):
    """Добавление комментария к стикеру"""
    sticker_id = int(callback.data.split(":")[1])

    await state.update_data(comment_sticker_id=sticker_id)
    await state.set_state(StickerGeneration.waiting_for_feedback_comment)

    await callback.message.answer(
        "💬 <b>Расскажите, что можно улучшить?</b>\n\n"
        "Ваш комментарий поможет мне создавать более точные стикеры.\n\n"
        "<i>Отправьте сообщение с вашим отзывом:</i>",
        parse_mode=ParseMode.HTML
    )

    await callback.answer()


# Обработчики сообщений
@dp.message(StateFilter(StickerGeneration.waiting_for_prompt), F.text)
async def handle_text_prompt(message: Message, state: FSMContext):
    """Обработка текстового описания"""
    prompt = message.text.strip()

    # Валидация
    if len(prompt) < 3:
        await message.answer("❌ Слишком короткое описание. Минимум 3 символа.")
        return

    if len(prompt) > 200:
        await message.answer("❌ Слишком длинное описание. Максимум 200 символов.")
        return

    # Сохраняем промпт
    await state.update_data(prompt=prompt)

    # Обновляем статистику
    await db_manager.update_message_stats(message.from_user.id, is_voice=False)

    # Переходим к выбору стиля
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

    # Проверяем размер
    if voice.file_size > 20 * 1024 * 1024:
        await message.answer("❌ Голосовое сообщение слишком большое. Максимум 20 МБ.")
        return

    # Отправляем уведомление о распознавании
    processing_msg = await message.answer("🎤 Распознаю речь...")

    try:
        # Скачиваем файл
        file_info = await bot.get_file(voice.file_id)
        file_path = STORAGE_DIR / f"voice_{message.from_user.id}_{voice.file_id}.ogg"

        await bot.download_file(file_info.file_path, file_path)

        # Распознаем речь
        result = await stt_service.transcribe_audio(str(file_path))

        if not result or not result.get("text"):
            await processing_msg.edit_text("❌ Не удалось распознать речь. Попробуйте еще раз.")
            return

        prompt = result["text"]

        # Сохраняем промпт
        await state.update_data(prompt=prompt)

        # Обновляем статистику
        await db_manager.update_message_stats(message.from_user.id, is_voice=True)

        # Удаляем сообщение о распознавании
        await processing_msg.delete()

        # Показываем распознанный текст и переходим к выбору стиля
        await message.answer(
            f"🎤 Распознано: <i>{prompt}</i>\n\n"
            f"🎨 <b>Выберите стиль стикера:</b>",
            reply_markup=create_style_keyboard(),
            parse_mode=ParseMode.HTML
        )

        await state.set_state(StickerGeneration.waiting_for_style)

        # Удаляем временный файл
        try:
            os.unlink(file_path)
        except:
            pass

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        await processing_msg.edit_text(MESSAGES["error"])


@dp.message(StateFilter(StickerGeneration.waiting_for_feedback_comment), F.text)
async def handle_feedback_comment(message: Message, state: FSMContext):
    """Обработка комментария к стикеру"""
    comment = message.text.strip()
    data = await state.get_data()
    sticker_id = data.get('comment_sticker_id')

    if sticker_id:
        # Сохраняем комментарий
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
    # Если пользователь просто отправил текст, предлагаем создать стикер
    current_state = await state.get_state()

    if not current_state:
        # Сохраняем промпт и переходим к выбору стиля
        await state.update_data(prompt=message.text.strip())
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
        # Устанавливаем состояние и обрабатываем голосовое
        await state.set_state(StickerGeneration.waiting_for_prompt)
        await handle_voice_prompt(message, state, message.bot)


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

        text += f"{i}. {sticker['prompt'][:30]}...{rating_text} ({created})\n"

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


# Обработчик для админских команд
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админская панель с расширенной статистикой"""
    if message.from_user.id not in ADMIN_IDS:
        return

    stats = await db_manager.get_total_stats()

    # Получаем статистику по оценкам
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
- Активных за 24ч: {stats['active_users_24h']}
- Средняя оценка: {stats['average_rating']}/5.0

⭐ Распределение оценок:
{ratings_text}

📈 Качество генерации постоянно улучшается!
"""

    await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(Command("ab_stats"))
async def cmd_ab_stats(message: Message):
    """Статистика A/B тестирования (только для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    from prompt_optimization import prompt_optimizer

    stats = prompt_optimizer.get_statistics()

    text = "📊 <b>A/B Testing Statistics</b>\n\n"

    # Текущий чемпион
    if stats["current_champion"]:
        text += f"🏆 <b>Current Champion:</b> {stats['current_champion']}\n\n"

    # Статистика по шаблонам
    text += "<b>Template Performance:</b>\n"
    for tid, data in stats["templates"].items():
        status = "✅" if data["active"] else "❌"
        text += (
            f"\n{status} <b>{data['name']}</b>\n"
            f"   • Uses: {data['uses']}\n"
            f"   • Success: {data['success_rate']}\n"
            f"   • Avg Rating: {data['avg_rating']}/5.0\n"
        )

    # Статистика за последние 7 дней
    if "recent_7_days" in stats:
        recent = stats["recent_7_days"]
        text += (
            f"\n📅 <b>Last 7 Days:</b>\n"
            f"• Tests: {recent['total_tests']}\n"
            f"• Avg Rating: {recent['avg_rating']:.2f}\n"
            f"• Success Rate: {recent['success_rate']:.1%}"
        )

    await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(Command("export_best"))
async def cmd_export_best(message: Message):
    """Экспорт лучших практик (только для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    from prompt_optimization import prompt_optimizer

    best_practices = prompt_optimizer.export_best_practices()

    if not best_practices:
        await message.answer("❌ Нет данных для экспорта")
        return

    text = "🌟 <b>Best Practices Export</b>\n\n"

    for practice in best_practices[:3]:  # Топ-3
        text += (
            f"<b>{practice['template']}</b>\n"
            f"Success: {practice['success_rate']:.1%} | "
            f"Rating: {practice['avg_rating']:.2f}\n"
        )

        # Примеры успешных запросов
        if practice['examples']:
            text += "Examples:\n"
            for ex in practice['examples'][:2]:
                text += f"  • \"{ex['request']}\" (⭐{ex['rating']})\n"
        text += "\n"

    await message.answer(text, parse_mode=ParseMode.HTML)

    # Сохраняем полный отчет
    import json
    with open('best_practices_export.json', 'w', encoding='utf-8') as f:
        json.dump(best_practices, f, indent=2, ensure_ascii=False)

    await message.answer("📄 Полный отчет сохранен в best_practices_export.json")


# Экспортируем диспетчер
__all__ = ["dp"]