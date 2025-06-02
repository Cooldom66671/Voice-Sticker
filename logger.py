"""
Система логирования для бота VoiceSticker
"""
import sys
from pathlib import Path
from loguru import logger
from config import LOG_LEVEL, LOG_FILE, LOGS_DIR

# Удаляем стандартный обработчик
logger.remove()

# Настраиваем формат логов
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# Добавляем вывод в консоль
logger.add(
    sys.stdout,
    format=log_format,
    level=LOG_LEVEL,
    colorize=True,
)

# Добавляем запись в файл
logger.add(
    LOG_FILE,
    format=log_format,
    level=LOG_LEVEL,
    rotation="1 day",  # Новый файл каждый день
    retention="7 days",  # Хранить логи 7 дней
    compression="zip",  # Сжимать старые логи
    encoding="utf-8",
)

# Создаем отдельный файл для ошибок
error_log_file = LOGS_DIR / "errors.log"
logger.add(
    error_log_file,
    format=log_format,
    level="ERROR",
    rotation="1 week",
    retention="1 month",
    compression="zip",
    encoding="utf-8",
)


# Функция для логирования с контекстом пользователя
def log_user_action(user_id: int, action: str, details: dict = None):
    """
    Логирует действия пользователя

    Args:
        user_id: ID пользователя Telegram
        action: Название действия
        details: Дополнительные детали
    """
    context = {
        "user_id": user_id,
        "action": action,
    }
    if details:
        context.update(details)

    logger.info(f"User action: {action}", **context)


# Декоратор для логирования функций
def log_function(func):
    """Декоратор для автоматического логирования вызовов функций"""

    async def async_wrapper(*args, **kwargs):
        logger.debug(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        try:
            result = await func(*args, **kwargs)
            logger.debug(f"{func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            raise

    def sync_wrapper(*args, **kwargs):
        logger.debug(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"{func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            raise

    # Возвращаем правильный wrapper в зависимости от типа функции
    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


# Функция для структурированного логирования ошибок
def log_error(error: Exception, context: dict = None):
    """
    Логирует ошибку с контекстом

    Args:
        error: Объект исключения
        context: Дополнительный контекст
    """
    error_info = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": True,
    }
    if context:
        error_info.update(context)

    logger.error(f"Error occurred: {error_info['error_type']}", **error_info)


# Экспортируем logger для использования в других модулях
__all__ = ["logger", "log_user_action", "log_function", "log_error"]