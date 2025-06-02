#!/usr/bin/env python3
"""
Скрипт для миграции базы данных VoiceSticker
Добавляет новые колонки для системы обратной связи
"""
import asyncio
import aiosqlite
from pathlib import Path


async def check_column_exists(db, table: str, column: str) -> bool:
    """Проверяет существование колонки в таблице"""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = await cursor.fetchall()
    return any(col[1] == column for col in columns)


async def migrate_database():
    """Выполняет миграцию базы данных"""
    db_path = Path("bot_database.db")

    if not db_path.exists():
        print("❌ База данных не найдена!")
        return

    print("🔄 Начинаю миграцию базы данных...")

    async with aiosqlite.connect(db_path) as db:
        # Проверяем и добавляем колонки
        columns_to_add = [
            ("rating", "INTEGER DEFAULT NULL"),
            ("metadata", "TEXT DEFAULT NULL"),
            ("feedback_comment", "TEXT DEFAULT NULL")
        ]

        for column_name, column_def in columns_to_add:
            if not await check_column_exists(db, "stickers", column_name):
                print(f"➕ Добавляю колонку {column_name}...")
                try:
                    await db.execute(f"ALTER TABLE stickers ADD COLUMN {column_name} {column_def}")
                    print(f"✅ Колонка {column_name} добавлена")
                except Exception as e:
                    print(f"⚠️  Ошибка при добавлении {column_name}: {e}")
            else:
                print(f"✔️  Колонка {column_name} уже существует")

        # Создаём индекс
        print("📊 Создаю индекс для рейтинга...")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_stickers_rating ON stickers(rating)")

        # Создаём представления
        print("📈 Создаю представления для аналитики...")

        # Представление для качества стикеров
        await db.execute("""
            CREATE VIEW IF NOT EXISTS sticker_quality_stats AS
            SELECT 
                u.user_id,
                u.username,
                COUNT(s.id) as total_stickers,
                COUNT(s.rating) as rated_stickers,
                AVG(s.rating) as avg_rating,
                COUNT(CASE WHEN s.rating >= 4 THEN 1 END) as good_stickers,
                COUNT(CASE WHEN s.rating <= 2 THEN 1 END) as poor_stickers
            FROM users u
            LEFT JOIN stickers s ON u.user_id = s.user_id AND s.is_deleted = 0
            GROUP BY u.user_id
        """)

        # Представление для анализа стилей
        await db.execute("""
            CREATE VIEW IF NOT EXISTS style_performance AS
            SELECT 
                style,
                COUNT(*) as total_generated,
                AVG(rating) as avg_rating,
                COUNT(CASE WHEN rating = 5 THEN 1 END) as perfect_count,
                COUNT(CASE WHEN rating >= 4 THEN 1 END) as good_count,
                COUNT(CASE WHEN rating <= 2 THEN 1 END) as poor_count
            FROM stickers
            WHERE rating IS NOT NULL AND is_deleted = 0
            GROUP BY style
            ORDER BY avg_rating DESC
        """)

        await db.commit()

        print("\n✅ Миграция завершена успешно!")

        # Показываем текущую структуру
        print("\n📋 Текущая структура таблицы stickers:")
        cursor = await db.execute("PRAGMA table_info(stickers)")
        columns = await cursor.fetchall()
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")


if __name__ == "__main__":
    asyncio.run(migrate_database())