"""
Утилиты для обработки стикеров
Упрощенная версия для работы с fofr/sticker-maker
Добавлена поддержка текста на стикерах
"""
import io
import os
from typing import Union, Optional, Tuple
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageFilter
from pathlib import Path

# Assuming 'logger' and 'log_function' are defined in a 'logger' module
from logger import logger, log_function


@log_function
async def add_text_to_sticker(
        image_data: Union[bytes, Image.Image],
        text: str,
        position: str = "bottom",
        font_scale: float = 1.0
) -> bytes:
    """
    Добавляет текст на стикер с красивым оформлением

    Args:
        image_data: Изображение в виде bytes или PIL Image
        text: Текст для добавления
        position: Позиция текста ('top', 'center', 'bottom')
        font_scale: Масштаб шрифта (1.0 = стандартный)

    Returns:
        bytes: Изображение с текстом в формате PNG
    """
    try:
        # Открываем изображение
        if isinstance(image_data, bytes):
            img = Image.open(io.BytesIO(image_data))
        else:
            img = image_data.copy()

        # Конвертируем в RGBA если нужно
        if img.mode != 'RGBA':
            img = img.convert('RGBA')

        # Создаем слой для текста
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)

        # Размер изображения
        width, height = img.size

        # Выбираем размер шрифта
        base_font_size = int(height * 0.1 * font_scale)
        if len(text) > 10:
            base_font_size = int(base_font_size * 0.8)  # Уменьшаем для длинного текста

        # Пытаемся загрузить красивый шрифт
        font = None
        font_paths = [
            # macOS
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Avenir.ttc",
            # Linux
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            # Windows
            "C:/Windows/Fonts/Arial.ttf",
            "C:/Windows/Fonts/impact.ttf",
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, base_font_size)
                    break
                except:
                    continue

        # Если не нашли шрифт, используем встроенный
        if not font:
            try:
                # Пробуем загрузить встроенный шрифт побольше
                font = ImageFont.load_default()
                # Для встроенного шрифта увеличиваем размер текста визуально
                text = text.upper()
            except:
                font = ImageFont.load_default()

        # Получаем размер текста
        text_upper = text.upper()
        bbox = draw.textbbox((0, 0), text_upper, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Добавляем отступы
        padding = int(height * 0.02)

        # Определяем позицию
        x = (width - text_width) // 2

        if position == "top":
            y = padding
        elif position == "center":
            y = (height - text_height) // 2
        else:  # bottom
            y = height - text_height - padding

        # Создаем эффект обводки
        outline_width = max(3, base_font_size // 12)

        # Рисуем тень для объема
        shadow_offset = max(2, base_font_size // 20)
        for offset in range(shadow_offset, 0, -1):
            alpha = int(80 * (1 - offset / shadow_offset))
            draw.text((x + offset, y + offset), text_upper,
                      font=font, fill=(0, 0, 0, alpha))

        # Рисуем белую обводку
        for adj_x in range(-outline_width, outline_width + 1):
            for adj_y in range(-outline_width, outline_width + 1):
                if adj_x != 0 or adj_y != 0:
                    draw.text((x + adj_x, y + adj_y), text_upper,
                              font=font, fill=(255, 255, 255, 255))

        # Рисуем основной текст
        draw.text((x, y), text_upper, font=font, fill=(20, 20, 20, 255))

        # Объединяем слои
        img = Image.alpha_composite(img, txt_layer)

        # Сохраняем результат
        output = io.BytesIO()
        img.save(output, format='PNG', optimize=True)
        output.seek(0)
        logger.info(f"Text '{text[:10]}...' added to sticker at position: {position}")
        return output.read()

    except Exception as e:
        logger.error(f"Error adding text to sticker: {e}")
        # Возвращаем оригинал при ошибке
        if isinstance(image_data, bytes):
            return image_data
        else:
            output = io.BytesIO()
            image_data.save(output, format='PNG')
            output.seek(0)
            return output.read()


async def prepare_sticker_for_telegram(image_bytes: bytes) -> bytes:
    """
    Подготавливает стикер строго по требованиям Telegram

    Требования Telegram:
    - PNG формат
    - 512x512 пикселей
    - Прозрачный фон (альфа-канал)
    - Размер файла < 512KB
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # 1. Конвертируем в RGBA для поддержки прозрачности
        if img.mode != 'RGBA':
            # Создаем альфа-канал
            img = img.convert('RGBA')

        # 2. Проверяем размер
        if img.size != (512, 512):
            img = img.resize((512, 512), Image.Resampling.LANCZOS)

        # 3. Убеждаемся что есть прозрачные пиксели (Telegram требует)
        # Проверяем альфа-канал
        has_transparency = False
        if img.mode == 'RGBA':
            alpha = img.split()[-1]
            if alpha.getextrema()[0] < 255:
                has_transparency = True

        # Если нет прозрачности, добавляем минимальную
        if not has_transparency:
            pixels = img.load()
            # Делаем угловой пиксель полупрозрачным
            if img.mode == 'RGBA':
                pixels[0, 0] = (pixels[0, 0][0], pixels[0, 0][1], pixels[0, 0][2], 254)

        # 4. Оптимизируем размер файла
        output = io.BytesIO()

        # Сохраняем с оптимизацией
        img.save(output, 'PNG', optimize=True, compress_level=9)
        output.seek(0)
        result = output.read()

        # Если файл слишком большой, уменьшаем качество
        if len(result) > 500 * 1024:  # 500KB для безопасности
            # Уменьшаем цвета
            img = img.convert('P', palette=Image.ADAPTIVE, colors=128)
            img = img.convert('RGBA')

            output = io.BytesIO()
            img.save(output, 'PNG', optimize=True, compress_level=9)
            output.seek(0)
            result = output.read()

        logger.info(f"Prepared sticker for Telegram: {len(result)} bytes, has_transparency: {has_transparency}")
        return result

    except Exception as e:
        logger.error(f"Error preparing sticker for Telegram: {e}")
        return image_bytes


@log_function
async def process_sticker(
        image_data: Union[bytes, Image.Image],
        target_size: Tuple[int, int] = (512, 512),
        enhance_quality: bool = True,
        add_text: Optional[str] = None,
        text_position: str = "bottom"
) -> bytes:
    """
    Обрабатывает изображение для использования как стикер

    Args:
        image_data: Изображение в виде bytes или PIL Image
        target_size: Целевой размер (по умолчанию 512x512)
        enhance_quality: Улучшить ли качество изображения
        add_text: Текст для добавления (опционально)
        text_position: Позиция текста ('top', 'center', 'bottom')

    Returns:
        bytes: Обработанное изображение в формате PNG
    """
    try:
        # Открываем изображение
        if isinstance(image_data, bytes):
            image = Image.open(io.BytesIO(image_data))
        else:
            image = image_data

        # Конвертируем в RGBA для поддержки прозрачности
        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        # Изменяем размер если нужно
        if image.size != target_size:
            # Сохраняем соотношение сторон
            image.thumbnail(target_size, Image.Resampling.LANCZOS)

            # Создаем новое изображение с целевым размером
            new_image = Image.new('RGBA', target_size, (0, 0, 0, 0))

            # Центрируем изображение
            x = (target_size[0] - image.width) // 2
            y = (target_size[1] - image.height) // 2
            new_image.paste(image, (x, y), image)
            image = new_image

        # Улучшаем качество если нужно
        if enhance_quality:
            # Повышаем контраст
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(1.1)

            # Повышаем резкость
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(1.2)

            # Повышаем насыщенность цветов
            enhancer = ImageEnhance.Color(image)
            image = enhancer.enhance(1.1)

        # Сохраняем в bytes
        output = io.BytesIO()
        image.save(output, format='PNG', optimize=True)
        output.seek(0)
        result_bytes = output.read()

        # Добавляем текст если нужно
        if add_text and add_text.strip():
            # Определяем размер шрифта в зависимости от позиции
            font_scale = 1.0
            if text_position == "center":
                font_scale = 1.2  # Больше для центра

            result_bytes = await add_text_to_sticker(
                result_bytes,
                add_text.strip(),
                text_position,
                font_scale
            )

        logger.info(f"Sticker processed successfully, size: {target_size}, text: {bool(add_text)}")

        # Подготавливаем для Telegram
        result_bytes = await prepare_sticker_for_telegram(result_bytes)

        return result_bytes

    except Exception as e:
        logger.error(f"Error processing sticker: {e}")
        # Возвращаем оригинал если обработка не удалась
        if isinstance(image_data, bytes):
            return image_data
        else:
            output = io.BytesIO()
            image_data.save(output, format='PNG')
            output.seek(0)
            return output.read()


@log_function
async def prepare_for_telegram(image_bytes: bytes) -> bytes:
    """
    Подготавливает изображение для отправки в Telegram (redirects to the stricter function)
    """
    logger.warning("prepare_for_telegram is deprecated. Use prepare_sticker_for_telegram instead.")
    return await prepare_sticker_for_telegram(image_bytes)


def get_sticker_info(file_path: Union[str, Path]) -> Optional[dict]:
    """
    Получает информацию о стикере

    Args:
        file_path: Путь к файлу стикера

    Returns:
        dict: Информация о стикере
    """
    try:
        image = Image.open(file_path)

        return {
            "size": image.size,
            "mode": image.mode,
            "format": image.format,
            "has_transparency": image.mode in ('RGBA', 'LA') or
                                (image.mode == 'P' and 'transparency' in image.info)
        }
    except Exception as e:
        logger.error(f"Error getting sticker info: {e}")
        return None


@log_function
async def validate_sticker_size(image_bytes: bytes) -> Tuple[bool, str]:
    """
    Проверяет, соответствует ли изображение требованиям Telegram для стикеров

    Args:
        image_bytes: Изображение в байтах

    Returns:
        (bool, str): (валидно, сообщение об ошибке если не валидно)
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Проверка размера файла (не более 512KB)
        if len(image_bytes) > 512 * 1024:
            return False, "Размер файла превышает 512KB"

        # Проверка размеров (должно быть 512x512)
        if img.width != 512 or img.height != 512:
            return False, f"Неверный размер: {img.width}x{img.height} (должно быть 512x512)"

        # Проверка формата (только PNG для static stickers)
        if img.format != 'PNG':
            return False, f"Неверный формат: {img.format} (должен быть PNG)"

        return True, "OK"

    except Exception as e:
        logger.error(f"Error validating sticker: {e}")
        return False, f"Ошибка проверки: {str(e)}"


# Для обратной совместимости
StickerProcessor = None  # Класс больше не используется
BackgroundStyle = None  # Enum больше не используется

try:
    from rembg import remove

    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False
    logger.warning("rembg not installed, background removal will be limited")


# Обновите функцию prepare_sticker_for_telegram:
@log_function
async def prepare_sticker_for_telegram(image_bytes: bytes) -> bytes:
    """
    Подготавливает стикер строго по требованиям Telegram
    """
    try:
        # Если rembg доступен, удаляем фон
        if REMBG_AVAILABLE:
            try:
                # Удаляем фон
                image_bytes = remove(image_bytes)
                logger.info("Background removed using rembg")
            except Exception as e:
                logger.warning(f"Failed to remove background with rembg: {e}")

        img = Image.open(io.BytesIO(image_bytes))

        # 1. Конвертируем в RGBA для поддержки прозрачности
        if img.mode != 'RGBA':
            img = img.convert('RGBA')

        # 2. Проверяем размер
        if img.size != (512, 512):
            img = img.resize((512, 512), Image.Resampling.LANCZOS)

        # 3. Сохраняем с оптимизацией
        output = io.BytesIO()
        img.save(output, 'PNG', optimize=True, compress_level=9)
        output.seek(0)
        result = output.read()

        # Проверяем размер файла
        if len(result) > 500 * 1024:
            # Уменьшаем качество если нужно
            img = img.convert('P', palette=Image.ADAPTIVE, colors=128)
            img = img.convert('RGBA')

            output = io.BytesIO()
            img.save(output, 'PNG', optimize=True, compress_level=9)
            output.seek(0)
            result = output.read()

        logger.info(f"Prepared sticker for Telegram: {len(result)} bytes")
        return result

    except Exception as e:
        logger.error(f"Error preparing sticker for Telegram: {e}")
        return image_bytes