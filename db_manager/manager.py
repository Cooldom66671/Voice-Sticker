"""
Менеджер базы данных для бота VoiceSticker - улучшенная версия
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
import aiosqlite
from pathlib import Path

from logger import logger, log_function
from config import DATABASE_PATH


class DatabaseManager:
    """Класс для работы с базой данных"""

    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        logger.info(f"Инициализация базы данных: {db_path}")

    async def init_db(self):
        """Инициализация базы данных и создание таблиц"""
        async with aiosqlite.connect(self.db_path) as db:
            # Включаем поддержку внешних ключей
            await db.execute("PRAGMA foreign_keys = ON")

            # Таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT DEFAULT 'ru',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица стикеров с новыми полями для обратной связи
            await db.execute("""
                CREATE TABLE IF NOT EXISTS stickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    style TEXT NOT NULL,
                    background TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_id TEXT,
                    rating INTEGER DEFAULT NULL,
                    metadata TEXT DEFAULT NULL,
                    feedback_comment TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_deleted BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # Таблица статистики пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    total_stickers INTEGER DEFAULT 0,
                    total_voice_messages INTEGER DEFAULT 0,
                    total_text_messages INTEGER DEFAULT 0,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # Таблица настроек пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    default_style TEXT DEFAULT 'default',
                    default_background TEXT DEFAULT 'transparent',
                    notifications_enabled BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # Таблица ошибок для аналитики
            await db.execute("""
                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Создаем индексы для оптимизации
            await db.execute("CREATE INDEX IF NOT EXISTS idx_stickers_user_id ON stickers(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_stickers_created_at ON stickers(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_stickers_rating ON stickers(rating)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_error_log_created_at ON error_log(created_at)")

            await db.commit()
            logger.info("База данных успешно инициализирована")

    async def execute(self, query: str, params: tuple = ()):
        """
        Выполняет SQL запрос (для INSERT, UPDATE, DELETE)
        ВАЖНО: Не используйте этот метод для SELECT запросов!
        Используйте fetchone() или fetchall() для SELECT.

        Args:
            query: SQL запрос
            params: Параметры для запроса

        Returns:
            lastrowid для INSERT или rowcount для UPDATE/DELETE
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            await db.commit()

            # Возвращаем полезную информацию в зависимости от типа запроса
            if query.strip().upper().startswith('INSERT'):
                return cursor.lastrowid
            else:
                return cursor.rowcount

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        """
        Выполняет запрос и возвращает одну строку

        Args:
            query: SQL запрос
            params: Параметры для запроса

        Returns:
            Строка результата или None
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> List[aiosqlite.Row]:
        """
        Выполняет запрос и возвращает все строки

        Args:
            query: SQL запрос
            params: Параметры для запроса

        Returns:
            Список строк результата
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            return await cursor.fetchall()

    async def fetch(self, query: str, params: tuple = ()):
        """
        Алиас для fetchall с возвратом курсора для совместимости
        DEPRECATED: Используйте fetchall() напрямую
        """
        class AsyncCursor:
            def __init__(self, rows):
                self._rows = rows

            async def fetchall(self):
                return self._rows

        rows = await self.fetchall(query, params)
        return AsyncCursor(rows)

    @log_function
    async def get_sticker_for_retry(self, sticker_id: int) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о стикере для повторной генерации

        Args:
            sticker_id: ID стикера

        Returns:
            Словарь с информацией о стикере или None
        """
        try:
            row = await self.fetchone(
                "SELECT prompt, style, background, user_id FROM stickers WHERE id = ? AND is_deleted = FALSE",
                (sticker_id,)
            )

            if row:
                return {
                    'prompt': row['prompt'],
                    'style': row['style'],
                    'background': row['background'],
                    'user_id': row['user_id']
                }
            return None

        except Exception as e:
            logger.error(f"Ошибка при получении стикера {sticker_id}: {e}")
            return None

    @log_function
    async def add_user(self, user_id: int, username: str = None,
                       first_name: str = None, last_name: str = None,
                       language_code: str = 'ru') -> bool:
        """Добавляет нового пользователя или обновляет существующего"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, language_code)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    language_code = excluded.language_code,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, username, first_name, last_name, language_code))

            # Создаем запись в статистике
            await db.execute("""
                INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)
            """, (user_id,))

            # Создаем запись в настройках
            await db.execute("""
                INSERT OR IGNORE INTO user_preferences (user_id) VALUES (?)
            """, (user_id,))

            await db.commit()
            return True

    @log_function
    async def save_sticker(self, user_id: int, prompt: str, style: str,
                           background: str, file_path: str, file_id: Optional[str] = None,
                           metadata: Optional[str] = None) -> int:
        """
        Сохраняет информацию о созданном стикере

        Args:
            user_id: ID пользователя
            prompt: Текст запроса
            style: Стиль стикера
            background: Тип фона
            file_path: Путь к файлу
            file_id: Telegram file_id
            metadata: JSON с метаданными генерации

        Returns:
            ID созданной записи
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO stickers (user_id, prompt, style, background, file_path, file_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, prompt, style, background, file_path, file_id, metadata))

            # Обновляем статистику пользователя
            await db.execute("""
                UPDATE user_stats 
                SET total_stickers = total_stickers + 1,
                    last_activity = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (user_id,))

            await db.commit()
            return cursor.lastrowid

    async def update_sticker_rating(self, sticker_id: int, user_id: int, rating: int) -> bool:
        """
        Обновляет рейтинг стикера

        Args:
            sticker_id: ID стикера
            user_id: ID пользователя (для проверки владельца)
            rating: Рейтинг от 1 до 5

        Returns:
            True если обновление успешно, False если стикер не найден
        """
        try:
            rowcount = await self.execute("""
                UPDATE stickers 
                SET rating = ?
                WHERE id = ? AND user_id = ? AND is_deleted = FALSE
            """, (rating, sticker_id, user_id))

            return rowcount > 0

        except Exception as e:
            logger.error(f"Ошибка при обновлении рейтинга стикера {sticker_id}: {e}")
            return False

    async def increment_user_stat(self, user_id: int, stat_name: str):
        """Увеличивает счетчик статистики пользователя"""
        if stat_name == 'stickers_created':
            await self.execute(
                "UPDATE user_stats SET total_stickers = total_stickers + 1 WHERE user_id = ?",
                (user_id,)
            )

    async def get_user_stickers(self, user_id: int, limit: int = 10,
                                offset: int = 0) -> List[Dict[str, Any]]:
        """Получает стикеры пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, prompt, style, background, file_path, file_id, rating, created_at
                FROM stickers
                WHERE user_id = ? AND is_deleted = FALSE
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (user_id, limit, offset))

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_user_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает статистику пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT 
                    u.user_id,
                    u.username,
                    u.first_name,
                    u.created_at as registered_at,
                    s.total_stickers,
                    s.total_voice_messages,
                    s.total_text_messages,
                    s.last_activity
                FROM users u
                LEFT JOIN user_stats s ON u.user_id = s.user_id
                WHERE u.user_id = ?
            """, (user_id,))

            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_message_stats(self, user_id: int, is_voice: bool = False):
        """Обновляет статистику сообщений"""
        async with aiosqlite.connect(self.db_path) as db:
            if is_voice:
                await db.execute("""
                    UPDATE user_stats 
                    SET total_voice_messages = total_voice_messages + 1,
                        last_activity = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (user_id,))
            else:
                await db.execute("""
                    UPDATE user_stats 
                    SET total_text_messages = total_text_messages + 1,
                        last_activity = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (user_id,))

            await db.commit()

    async def get_user_preferences(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает настройки пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT default_style, default_background, notifications_enabled
                FROM user_preferences
                WHERE user_id = ?
            """, (user_id,))

            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_user_preferences(self, user_id: int, **kwargs):
        """Обновляет настройки пользователя"""
        allowed_fields = ['default_style', 'default_background', 'notifications_enabled']
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return

        fields = ', '.join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [user_id]

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"""
                UPDATE user_preferences 
                SET {fields}
                WHERE user_id = ?
            """, values)
            await db.commit()

    async def log_error(self, error_type: str, error_message: str,
                        user_id: int = None, context: str = None):
        """Логирует ошибку в базу данных"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO error_log (user_id, error_type, error_message, context)
                VALUES (?, ?, ?, ?)
            """, (user_id, error_type, error_message, context))
            await db.commit()

    async def delete_sticker(self, sticker_id: int, user_id: int) -> bool:
        """Мягкое удаление стикера"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE stickers 
                SET is_deleted = TRUE
                WHERE id = ? AND user_id = ?
            """, (sticker_id, user_id))

            await db.commit()
            return cursor.rowcount > 0

    async def get_total_stats(self) -> Dict[str, Any]:
        """Получает общую статистику бота (для админов)"""
        async with aiosqlite.connect(self.db_path) as db:
            # Общее количество пользователей
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]

            # Общее количество стикеров
            cursor = await db.execute("SELECT COUNT(*) FROM stickers WHERE is_deleted = FALSE")
            total_stickers = (await cursor.fetchone())[0]

            # Активные пользователи за последние 24 часа
            cursor = await db.execute("""
                SELECT COUNT(DISTINCT user_id) FROM user_stats
                WHERE last_activity > datetime('now', '-1 day')
            """)
            active_users_24h = (await cursor.fetchone())[0]

            # Средний рейтинг стикеров
            cursor = await db.execute("""
                SELECT AVG(rating) FROM stickers 
                WHERE rating IS NOT NULL AND is_deleted = FALSE
            """)
            avg_rating = (await cursor.fetchone())[0] or 0

            return {
                "total_users": total_users,
                "total_stickers": total_stickers,
                "active_users_24h": active_users_24h,
                "average_rating": round(avg_rating, 2)
            }


# Создаем единственный экземпляр менеджера
db_manager = DatabaseManager()

# Экспортируем для удобства импорта
__all__ = ["db_manager", "DatabaseManager"]