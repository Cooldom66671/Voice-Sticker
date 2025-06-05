# sticker_utils/__init__.py

# Импортируем только то, что действительно есть в utils.py
from .utils import (
    process_sticker,
    prepare_for_telegram, # Добавлено из simplified_sticker_utils_final.py
    get_sticker_info # Добавлено из simplified_sticker_utils_final.py
)

# Для обратной совместимости, если где-то используются старые импорты
__all__ = [
    'process_sticker',
    'prepare_for_telegram',
    'get_sticker_info'
]

# Для обратной совместимости явно устанавливаем None для старых классов, которые больше не используются.
# Это предотвратит ошибки NameError, если они где-то импортируются напрямую.
StickerProcessor = None
BackgroundStyle = None