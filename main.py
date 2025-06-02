"""
Главный файл для запуска бота VoiceSticker
"""
import asyncio
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from logger import logger
from config import (
    BOT_TOKEN, WEBHOOK_ENABLED, WEBHOOK_HOST,
    WEBHOOK_PATH, WEBHOOK_PORT, DEBUG
)
from bot_handlers import dp
from db_manager import db_manager
from image_generation_service import image_service
from stt_service import stt_service


async def on_startup():
    """Действия при запуске бота"""
    logger.info("Запуск бота VoiceSticker...")

    # Инициализация базы данных
    logger.info("Инициализация базы данных...")
    await db_manager.init_db()

    # Инициализация сервисов
    logger.info("Инициализация сервисов...")
    await image_service.initialize()
    await stt_service.initialize()

    logger.info("✅ Бот успешно запущен!")


async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("Остановка бота...")

    # Здесь можно добавить очистку ресурсов

    logger.info("Бот остановлен")


async def main():
    """Основная функция запуска"""
    # Создаем экземпляр бота
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    # Регистрируем обработчики startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        if WEBHOOK_ENABLED:
            # Режим webhook для production
            logger.info(f"Запуск в режиме webhook: {WEBHOOK_HOST}{WEBHOOK_PATH}")

            # Устанавливаем webhook
            await bot.set_webhook(
                url=f"{WEBHOOK_HOST}{WEBHOOK_PATH}",
                drop_pending_updates=True
            )

            # Запускаем webhook
            from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
            from aiohttp import web

            app = web.Application()
            webhook_requests_handler = SimpleRequestHandler(
                dispatcher=dp,
                bot=bot
            )
            webhook_requests_handler.register(app, path=WEBHOOK_PATH)
            setup_application(app, dp, bot=bot)

            await web._run_app(app, host="0.0.0.0", port=WEBHOOK_PORT)
        else:
            # Режим polling для разработки
            logger.info("Запуск в режиме polling (разработка)")

            # Удаляем webhook если был установлен
            await bot.delete_webhook(drop_pending_updates=True)

            # Запускаем polling
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=True
            )

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

    finally:
        # Закрываем соединение с ботом
        await bot.session.close()


if __name__ == "__main__":
    # Настройка для Windows
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        # Запускаем бота
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        sys.exit(1)