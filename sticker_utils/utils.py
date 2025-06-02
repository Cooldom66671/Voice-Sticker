"""
Утилиты для обработки и оптимизации стикеров
"""
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from typing import Optional, Tuple, Union
from pathlib import Path
import io
from enum import Enum

from logger import logger
from config import STICKER_MAX_SIZE, STICKER_FILE_SIZE_LIMIT


class BackgroundStyle(Enum):
    """Стили фонов для стикеров"""
    TRANSPARENT = "transparent"
    WHITE = "white"
    GRADIENT = "gradient"
    CIRCLE = "circle"
    ROUNDED = "rounded"


class StickerProcessor:
    """Класс для обработки стикеров"""

    @staticmethod
    def apply_background(image: Image.Image, style: BackgroundStyle) -> Image.Image:
        """
        Применяет фон к изображению

        Args:
            image: Исходное изображение
            style: Стиль фона

        Returns:
            Обработанное изображение
        """
        # Убеждаемся, что изображение в RGBA
        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        width, height = image.size

        if style == BackgroundStyle.TRANSPARENT:
            # Уже прозрачный, ничего не делаем
            return image

        elif style == BackgroundStyle.WHITE:
            # Белый фон
            background = Image.new('RGBA', (width, height), (255, 255, 255, 255))
            background.paste(image, (0, 0), image)
            return background

        elif style == BackgroundStyle.GRADIENT:
            # Градиентный фон
            background = StickerProcessor._create_gradient(width, height)
            background.paste(image, (0, 0), image)
            return background

        elif style == BackgroundStyle.CIRCLE:
            # Круглый стикер
            return StickerProcessor._make_circle(image)

        elif style == BackgroundStyle.ROUNDED:
            # Закругленные углы
            return StickerProcessor._make_rounded(image, radius=50)

        return image

    @staticmethod
    def _create_gradient(width: int, height: int) -> Image.Image:
        """Создает градиентный фон"""
        gradient = Image.new('RGBA', (width, height))
        draw = ImageDraw.Draw(gradient)

        # Градиент от светло-розового к светло-голубому
        for y in range(height):
            r = int(255 - (y / height) * 50)  # 255 -> 205
            g = int(200 + (y / height) * 50)  # 200 -> 250
            b = int(220 + (y / height) * 35)  # 220 -> 255
            draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

        return gradient

    @staticmethod
    def _make_circle(image: Image.Image) -> Image.Image:
        """Делает изображение круглым"""
        size = min(image.size)

        # Создаем квадратное изображение
        square = Image.new('RGBA', (size, size), (0, 0, 0, 0))

        # Вставляем исходное изображение по центру
        paste_x = (size - image.width) // 2
        paste_y = (size - image.height) // 2
        square.paste(image, (paste_x, paste_y))

        # Создаем круглую маску
        mask = Image.new('L', (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)

        # Применяем маску
        output = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        output.paste(square, (0, 0))
        output.putalpha(mask)

        return output

    @staticmethod
    def _make_rounded(image: Image.Image, radius: int = 50) -> Image.Image:
        """Делает закругленные углы"""
        # Создаем маску с закругленными углами
        mask = Image.new('L', image.size, 0)
        draw = ImageDraw.Draw(mask)

        # Рисуем прямоугольник с закругленными углами
        draw.rounded_rectangle(
            [(0, 0), image.size],
            radius=radius,
            fill=255
        )

        # Применяем маску
        output = Image.new('RGBA', image.size, (0, 0, 0, 0))
        output.paste(image, (0, 0))
        output.putalpha(mask)

        return output

    @staticmethod
    def add_shadow(image: Image.Image, offset: Tuple[int, int] = (5, 5),
                   blur_radius: int = 10, shadow_color: Tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
        """
        Добавляет тень к изображению

        Args:
            image: Исходное изображение
            offset: Смещение тени (x, y)
            blur_radius: Радиус размытия тени
            shadow_color: Цвет тени

        Returns:
            Изображение с тенью
        """
        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        # Создаем изображение большего размера для тени
        total_width = image.width + abs(offset[0]) + blur_radius * 2
        total_height = image.height + abs(offset[1]) + blur_radius * 2

        shadow_image = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 0))

        # Создаем тень
        shadow = Image.new('RGBA', image.size, shadow_color + (150,))

        # Позиция для тени
        shadow_x = blur_radius + max(0, offset[0])
        shadow_y = blur_radius + max(0, offset[1])

        # Вставляем тень
        shadow_image.paste(shadow, (shadow_x, shadow_y), image)

        # Размываем тень
        shadow_image = shadow_image.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        # Вставляем оригинальное изображение
        image_x = blur_radius + max(0, -offset[0])
        image_y = blur_radius + max(0, -offset[1])
        shadow_image.paste(image, (image_x, image_y), image)

        return shadow_image

    @staticmethod
    def add_outline(image: Image.Image, outline_width: int = 3,
                    outline_color: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> Image.Image:
        """
        Добавляет обводку к изображению

        Args:
            image: Исходное изображение
            outline_width: Толщина обводки
            outline_color: Цвет обводки

        Returns:
            Изображение с обводкой
        """
        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        # Создаем изображение большего размера
        outline_image = Image.new('RGBA',
                                  (image.width + outline_width * 2,
                                   image.height + outline_width * 2),
                                  (0, 0, 0, 0))

        # Создаем обводку путем вставки изображения со смещением
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx * dx + dy * dy <= outline_width * outline_width:
                    outline_image.paste(outline_color,
                                        (outline_width + dx, outline_width + dy),
                                        image)

        # Вставляем оригинальное изображение сверху
        outline_image.paste(image, (outline_width, outline_width), image)

        return outline_image

    @staticmethod
    def resize_sticker(image: Image.Image, max_size: int = STICKER_MAX_SIZE) -> Image.Image:
        """
        Изменяет размер стикера с сохранением пропорций

        Args:
            image: Исходное изображение
            max_size: Максимальный размер стороны

        Returns:
            Изображение правильного размера
        """
        # Сохраняем пропорции
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Создаем квадратное изображение
        if image.width != image.height:
            size = max(image.width, image.height)
            square = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            paste_x = (size - image.width) // 2
            paste_y = (size - image.height) // 2
            square.paste(image, (paste_x, paste_y))
            return square

        return image

    @staticmethod
    def optimize_file_size(image: Image.Image, max_file_size: int = STICKER_FILE_SIZE_LIMIT,
                           format: str = 'PNG') -> bytes:
        """
        Оптимизирует размер файла

        Args:
            image: Изображение
            max_file_size: Максимальный размер файла в байтах
            format: Формат файла (PNG или WEBP)

        Returns:
            Байты оптимизированного изображения
        """
        # Начинаем с максимального качества
        quality = 95

        while quality > 10:
            # Сохраняем в буфер
            buffer = io.BytesIO()

            if format == 'PNG':
                image.save(buffer, format='PNG', optimize=True)
            else:
                image.save(buffer, format='WEBP', quality=quality, method=6)

            # Проверяем размер
            size = buffer.tell()

            if size <= max_file_size:
                buffer.seek(0)
                return buffer.getvalue()

            # Уменьшаем качество или размер
            if quality > 50:
                quality -= 10
            else:
                # Уменьшаем размер изображения
                new_size = int(image.width * 0.9)
                image = StickerProcessor.resize_sticker(image, new_size)
                quality = 95

        # Если все еще слишком большой, возвращаем с минимальным качеством
        buffer = io.BytesIO()
        image.save(buffer, format='WEBP', quality=10, method=6)
        buffer.seek(0)
        return buffer.getvalue()

    @staticmethod
    def remove_background(image: Image.Image) -> Optional[Image.Image]:
        """
        Удаляет фон с изображения (заглушка для MVP)

        Args:
            image: Исходное изображение

        Returns:
            Изображение без фона или None при ошибке
        """
        # В MVP версии просто возвращаем изображение как есть
        # В полной версии здесь будет использоваться rembg
        logger.warning("Удаление фона доступно только в полной версии")
        return image

    @staticmethod
    def create_sticker_preview(image: Image.Image, size: Tuple[int, int] = (128, 128)) -> Image.Image:
        """
        Создает превью стикера для быстрого просмотра

        Args:
            image: Исходное изображение
            size: Размер превью

        Returns:
            Уменьшенное изображение
        """
        preview = image.copy()
        preview.thumbnail(size, Image.Resampling.LANCZOS)
        return preview


# Функция для быстрой обработки стикера со всеми эффектами
async def process_sticker(image_input: Union[Image.Image, bytes],
                          background: str = "transparent",
                          add_shadow: bool = True,
                          add_outline: bool = False,
                          add_border: bool = False,  # Для обратной совместимости
                          border_size: int = 10) -> bytes:
    """
    Полная обработка стикера

    Args:
        image_input: Исходное изображение (PIL Image объект или байты)
        background: Стиль фона ('transparent', 'white', 'gradient', 'circle', 'rounded')
        add_shadow: Добавить тень
        add_outline: Добавить обводку
        add_border: Для обратной совместимости (использует add_outline)
        border_size: Размер обводки (если add_outline или add_border = True)

    Returns:
        Байты готового стикера
    """
    try:
        # Проверяем тип входных данных и конвертируем если нужно
        if isinstance(image_input, bytes):
            # Если получили байты, конвертируем в PIL Image
            image = Image.open(io.BytesIO(image_input))
            logger.info("Конвертировал байты в PIL Image")
        elif hasattr(image_input, 'mode'):  # Проверяем, что это PIL Image
            image = image_input
            logger.info("Получен PIL Image объект")
        else:
            raise ValueError(f"Неподдерживаемый тип входных данных: {type(image_input)}")

        # Убеждаемся, что изображение в RGBA
        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        # Применяем фон
        bg_style = BackgroundStyle(background)
        processed = StickerProcessor.apply_background(image, bg_style)

        # Добавляем эффекты
        # Поддержка обратной совместимости: add_border теперь работает как add_outline
        if add_outline or add_border:
            processed = StickerProcessor.add_outline(processed, outline_width=border_size)

        if add_shadow and background not in ["circle", "rounded"]:
            processed = StickerProcessor.add_shadow(processed)

        # Изменяем размер
        processed = StickerProcessor.resize_sticker(processed)

        # Оптимизируем размер файла
        return StickerProcessor.optimize_file_size(processed, format='PNG')

    except Exception as e:
        logger.error(f"Ошибка при обработке стикера: {e}")
        raise


# Экспортируем классы и функции
__all__ = ["StickerProcessor", "BackgroundStyle", "process_sticker"]