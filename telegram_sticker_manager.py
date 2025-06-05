import io
import asyncio
from typing import Optional, Tuple, List
from PIL import Image
from aiogram import Bot
from aiogram.types import InputSticker, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

from logger import logger
from config import BOT_USERNAME, MAX_STICKERS_PER_PACK, MAX_PACKS_PER_USER
import db_manager

# Определяем формат стикера
STICKER_FORMAT = "static"


class TelegramStickerManager:
    """Менеджер для работы с Telegram стикерами"""

    def __init__(self):
        self.bot_username = BOT_USERNAME

        # Словарь для транслитерации
        self.translit_dict = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
            'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
            'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
            'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
            'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch',
            'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
            'э': 'e', 'ю': 'yu', 'я': 'ya'
        }

    def _translit_name(self, name: str) -> str:
        """Транслитерация имени для использования в имени пака"""
        result = ''
        for char in name.lower():
            result += self.translit_dict.get(char, char)
        # Оставляем только буквы, цифры и подчеркивания
        result = ''.join(c for c in result if c.isalnum() or c == '_')
        return result[:32]  # Максимум 32 символа

    def _get_pack_name(self, user_id: int, pack_number: int = 1, user_name: Optional[str] = None) -> str:
        """Генерирует имя стикерпака для пользователя"""
        if user_name and pack_number == 1:
            # Для первого пака используем имя пользователя
            name_translit = self._translit_name(user_name)
            return f"{name_translit}_stickers_by_{self.bot_username}"
        else:
            # Для последующих паков или если нет имени
            return f"pack{pack_number}_{user_id}_by_{self.bot_username}"

    def _get_pack_title(self, user_name: str, pack_number: int = 1) -> str:
        """Генерирует заголовок стикерпака"""
        if pack_number > 1:
            return f"{user_name}'s stickers vol.{pack_number}"
        return f"{user_name}'s stickers"

    def _get_possible_pack_names(self, user_id: int, user_name: str) -> List[str]:
        """Возвращает список возможных имен паков для пользователя"""
        names = []

        # Транслитерированное имя
        name_translit = self._translit_name(user_name)

        # Основные варианты
        names.append(f"{name_translit}_stickers_by_{self.bot_username}")
        names.append(f"{name_translit}stickers_by_{self.bot_username}")

        # Варианты с номерами
        for i in range(1, 11):
            names.append(f"pack{i}_{user_id}_by_{self.bot_username}")
            if i > 1:
                names.append(f"{name_translit}_stickers{i}_by_{self.bot_username}")
                names.append(f"{name_translit}stickers{i}_by_{self.bot_username}")

        return names

    async def prepare_sticker_file(self, image_bytes: bytes) -> bytes:
        """
        Подготавливает стикер в правильном формате для Telegram.
        Конвертирует в WebP для лучшей совместимости.
        """
        try:
            # Сначала пробуем использовать prepare_sticker_for_telegram если доступна
            try:
                # Assuming prepare_sticker_for_telegram is defined elsewhere and handles initial preparation
                # This line is kept from the original request, but prepare_sticker_for_telegram is not provided in the context.
                # If it's a new function, it needs to be imported or defined.
                # image_bytes = await prepare_sticker_for_telegram(image_bytes)
                pass  # Keeping 'pass' as a placeholder since `prepare_sticker_for_telegram` is undefined.
            except:
                pass

            img = Image.open(io.BytesIO(image_bytes))

            # Конвертируем в RGBA
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

            # Точный размер 512x512
            if img.size != (512, 512):
                img = img.resize((512, 512), Image.Resampling.LANCZOS)

            # Конвертируем в WebP (Telegram лучше работает с WebP)
            output = io.BytesIO()
            img.save(output, 'WEBP', quality=99, method=6)
            output.seek(0)

            result = output.read()
            logger.info(f"Prepared sticker for Telegram: {len(result)} bytes (WebP format)")
            return result

        except Exception as e:
            logger.error(f"Error preparing sticker: {e}")
            return image_bytes

    async def create_sticker_pack(
            self,
            bot: Bot,
            user_id: int,
            user_name: str,
            first_sticker_bytes: bytes,
            emoji: str = "🎨",
            pack_name: Optional[str] = None,
            pack_number: int = 1
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Создает новый стикерпак для пользователя
        """
        if not pack_name:
            pack_name = self._get_pack_name(user_id, pack_number, user_name)
        pack_title = self._get_pack_title(user_name, pack_number)
        try:
            # Подготавливаем стикер в WebP формат
            prepared_bytes = await self.prepare_sticker_file(first_sticker_bytes)

            # Создаем BufferedInputFile
            sticker_file = BufferedInputFile(
                file=prepared_bytes,
                filename="sticker.webp"
            )

            # Создаем InputSticker
            sticker = InputSticker(
                sticker=sticker_file,
                emoji_list=[emoji],
                format="static"
            )

            # Создаем стикерпак
            result = await bot.create_new_sticker_set(
                user_id=user_id,
                name=pack_name,
                title=pack_title,
                stickers=[sticker],
                sticker_format="static"  # Добавляем явное указание формата
            )

            if result:
                # Проверяем создание
                await asyncio.sleep(1)
                try:
                    created_set = await bot.get_sticker_set(pack_name)
                    if created_set and len(created_set.stickers) > 0:
                        logger.info(f"Successfully created pack {pack_name} with {len(created_set.stickers)} stickers")
                        return True, pack_name, None
                except Exception as e:
                    logger.error(f"Error verifying created sticker pack: {e}")  # Log error for verification failure
                    pass

                return False, pack_name, "Failed to create sticker pack"

        except TelegramBadRequest as e:
            error_msg = str(e)
            logger.error(f"Telegram error creating pack: {error_msg}")

            if "STICKERSET_INVALID" in error_msg:
                return False, pack_name, "pack_exists"

            return False, pack_name, error_msg

        except Exception as e:
            logger.error(f"Error creating sticker pack: {e}")
            return False, pack_name, str(e)

    async def add_sticker_to_pack(
            self,
            bot: Bot,
            user_id: int,
            pack_name: str,
            sticker_bytes: bytes,
            emoji: str = "🎨"
    ) -> Tuple[bool, str]:
        """
        Добавляет стикер в существующий стикерпак с улучшенной обработкой ошибок

        Returns:
            Tuple[bool, str]: (успех, сообщение об ошибке если есть)
        """
        try:
            # Сначала проверяем, существует ли пак
            try:
                existing_pack = await bot.get_sticker_set(pack_name)
                logger.info(f"Pack {pack_name} has {len(existing_pack.stickers)} stickers before adding")
            except Exception as e:
                if "STICKERSET_INVALID" in str(e):
                    logger.warning(f"Pack {pack_name} is invalid, will need to create new one")
                    return False, "STICKERSET_INVALID"
                else:
                    logger.error(f"Error checking pack {pack_name}: {e}")
                    return False, str(e)

            # Подготавливаем стикер
            sticker_file = await self.prepare_sticker_file(sticker_bytes)

            # Создаем InputSticker
            input_sticker = InputSticker(
                sticker=BufferedInputFile(
                    file=sticker_file,
                    filename="sticker.png"
                ),
                emoji_list=[emoji],
                format="static"
            )

            # Пытаемся добавить стикер
            result = await bot.add_sticker_to_set(
                user_id=user_id,
                name=pack_name,
                sticker=input_sticker
            )

            if result:
                # Проверяем количество стикеров после добавления
                await asyncio.sleep(2)
                updated_pack = await bot.get_sticker_set(pack_name)
                logger.info(
                    f"Successfully added sticker to pack {pack_name}. Now has {len(updated_pack.stickers)} stickers")
                return True, ""
            else:
                logger.error(f"Failed to add sticker to pack {pack_name}")
                return False, "Failed to add sticker"

        except Exception as e:
            error_str = str(e)
            logger.error(f"Telegram error adding sticker: {e}")

            # Особая обработка для STICKERSET_INVALID
            if "STICKERSET_INVALID" in error_str:
                return False, "STICKERSET_INVALID"
            # Проверка на переполнение пака
            elif "STICKERS_TOO_MANY" in error_str or "STICKERS_TOO_MUCH" in error_str:
                return False, "pack_full"
            else:
                return False, error_str

    async def get_or_create_user_pack(
            self,
            bot: Bot,
            user_id: int,
            user_name: str,
            sticker_bytes: bytes,
            emoji: str = "🎨"
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Получает существующий или создает новый стикерпак для пользователя
        с улучшенной обработкой недействительных паков

        Returns:
            Tuple[bool, str, Optional[str]]: (успех, название пака, ошибка если есть)
        """
        # Получаем существующие паки из БД
        existing_packs = await db_manager.get_user_packs(user_id)

        # Пробуем добавить в существующие паки
        for pack in existing_packs:
            pack_name = pack['pack_name']
            logger.info(f"Trying to add to existing pack: {pack_name}")

            success, error = await self.add_sticker_to_pack(
                bot, user_id, pack_name, sticker_bytes, emoji
            )

            if success:
                logger.info(f"Added sticker to existing pack: {pack_name}")
                return True, pack_name, None

            elif error == "STICKERSET_INVALID":
                # Пак недействителен - удаляем из БД
                logger.warning(f"Pack {pack_name} is invalid, removing from DB")
                await db_manager.execute(
                    "DELETE FROM user_sticker_packs WHERE user_id = ? AND pack_name = ?",
                    (user_id, pack_name)
                )
                continue

            elif error == "pack_full":
                logger.info(f"Pack {pack_name} is full, trying next pack")
                continue

        # Если не удалось добавить в существующие паки, создаем новый
        if len(existing_packs) >= MAX_PACKS_PER_USER:
            logger.error(f"User {user_id} has reached max packs limit")
            return False, "", "Достигнут лимит стикерпаков (10)"

        # Генерируем имя для нового пака
        pack_number = len(existing_packs) + 1
        new_pack_name = self._get_pack_name(user_id, pack_number, user_name if pack_number == 1 else None)

        # Создаем новый пак
        success, pack_name, error = await self.create_sticker_pack(
            bot, user_id, user_name, sticker_bytes, emoji, new_pack_name, pack_number
        )

        if success:
            logger.info(f"Created new pack: {new_pack_name}")
            return True, pack_name, None
        else:
            return False, "", error or "Не удалось создать новый стикерпак"

    async def cleanup_invalid_packs(self, bot: Bot, user_id: int) -> int:
        """
        Проверяет и удаляет недействительные паки из БД

        Returns:
            int: количество удаленных паков
        """
        existing_packs = await db_manager.get_user_packs(user_id)
        removed_count = 0

        for pack in existing_packs:
            pack_name = pack['pack_name']
            try:
                # Пробуем получить информацию о паке
                await bot.get_sticker_set(pack_name)
                logger.info(f"Pack {pack_name} is valid")
            except Exception as e:
                if "STICKERSET_INVALID" in str(e):
                    # Удаляем недействительный пак из БД
                    logger.warning(f"Removing invalid pack {pack_name} from DB")
                    await db_manager.execute(
                        "DELETE FROM user_sticker_packs WHERE user_id = ? AND pack_name = ?",
                        (user_id, pack_name)
                    )
                    removed_count += 1
                else:
                    logger.error(f"Error checking pack {pack_name}: {e}")

        return removed_count

    def get_pack_link(self, pack_name: str) -> str:
        """Возвращает ссылку на стикерпак"""
        return f"https://t.me/addstickers/{pack_name}"


# Создаем единственный экземпляр менеджера
sticker_manager = TelegramStickerManager()

__all__ = ["sticker_manager", "TelegramStickerManager"]