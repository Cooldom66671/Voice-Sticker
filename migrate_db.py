#!/usr/bin/env python3
"""
Скрипт для миграции базы данных VoiceSticker
Добавляет:
- Колонки для системы обратной связи
- Таблицы для стикерпаков
"""
import asyncio
import aiosqlite
from pathlib import Path
from datetime import datetime


async def check_column_exists(db, table: str, column: str) -> bool:
    """Проверяет существование колонки в таблице"""
    try:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = await cursor.fetchall()
        return any(col[1] == column for col in columns)
    except:
        return False


async def check_table_exists(db, table: str) -> bool:
    """Проверяет существование таблицы"""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    )
    result = await cursor.fetchone()
    return result is not None


async def migrate_database():
    """Выполняет миграцию базы данных"""
    # Поддержка разных имен БД
    db_paths = [
        Path("bot_database.db"),
        Path("voicesticker.db"),
        Path("database.db")
    ]

    db_path = None
    for path in db_paths:
        if path.exists():
            db_path = path
            break

    if not db_path:
        print("❌ База данных не найдена!")
        print("Проверьте наличие файла: bot_database.db, voicesticker.db или database.db")
        return

    print(f"🔄 Начинаю миграцию базы данных: {db_path}")
    print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    async with aiosqlite.connect(db_path) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        # ========== МИГРАЦИЯ СУЩЕСТВУЮЩИХ ТАБЛИЦ ==========
        print("📋 Проверяю существующие таблицы...")

        # Проверяем и добавляем колонки в таблицу stickers
        if await check_table_exists(db, "stickers"):
            print("\n🔧 Обновляю таблицу stickers...")

            columns_to_add = [
                ("rating", "INTEGER DEFAULT NULL"),
                ("metadata", "TEXT DEFAULT NULL"),
                ("feedback_comment", "TEXT DEFAULT NULL")
            ]

            for column_name, column_def in columns_to_add:
                if not await check_column_exists(db, "stickers", column_name):
                    print(f"  ➕ Добавляю колонку {column_name}...")
                    try:
                        await db.execute(f"ALTER TABLE stickers ADD COLUMN {column_name} {column_def}")
                        print(f"  ✅ Колонка {column_name} добавлена")
                    except Exception as e:
                        print(f"  ⚠️  Ошибка при добавлении {column_name}: {e}")
                else:
                    print(f"  ✔️  Колонка {column_name} уже существует")

        # ========== СОЗДАНИЕ НОВЫХ ТАБЛИЦ ДЛЯ СТИКЕРПАКОВ ==========
        print("\n📦 Создаю таблицы для стикерпаков...")

        # Таблица стикерпаков
        if not await check_table_exists(db, "user_sticker_packs"):
            print("  ➕ Создаю таблицу user_sticker_packs...")
            await db.execute("""
                CREATE TABLE user_sticker_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pack_name TEXT NOT NULL UNIQUE,
                    pack_number INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            print("  ✅ Таблица user_sticker_packs создана")
        else:
            print("  ✔️  Таблица user_sticker_packs уже существует")

        # Таблица связей стикеров с паками
        if not await check_table_exists(db, "sticker_pack_items"):
            print("  ➕ Создаю таблицу sticker_pack_items...")
            await db.execute("""
                CREATE TABLE sticker_pack_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    sticker_id INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pack_id) REFERENCES user_sticker_packs (id),
                    FOREIGN KEY (sticker_id) REFERENCES stickers (id),
                    UNIQUE(pack_id, sticker_id)
                )
            """)
            print("  ✅ Таблица sticker_pack_items создана")
        else:
            print("  ✔️  Таблица sticker_pack_items уже существует")

        # ========== СОЗДАНИЕ ИНДЕКСОВ ==========
        print("\n📊 Создаю индексы...")

        indices = [
            ("idx_stickers_rating", "stickers(rating)", "stickers"),
            ("idx_user_packs", "user_sticker_packs(user_id)", "user_sticker_packs"),
            ("idx_pack_items", "sticker_pack_items(pack_id)", "sticker_pack_items"),
            ("idx_pack_items_sticker", "sticker_pack_items(sticker_id)", "sticker_pack_items")
        ]

        for index_name, index_def, table_name in indices:
            if await check_table_exists(db, table_name):
                try:
                    await db.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}")
                    print(f"  ✅ Индекс {index_name} создан или обновлен")
                except Exception as e:
                    print(f"  ⚠️  Ошибка при создании индекса {index_name}: {e}")

        # ========== СОЗДАНИЕ ПРЕДСТАВЛЕНИЙ ==========
        print("\n📈 Создаю представления для аналитики...")

        # Удаляем старые представления (если есть) для обновления
        views_to_create = [
            "sticker_quality_stats",
            "style_performance",
            "pack_statistics",
            "user_pack_summary"
        ]

        for view_name in views_to_create:
            try:
                await db.execute(f"DROP VIEW IF EXISTS {view_name}")
            except:
                pass

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
        print("  ✅ Представление sticker_quality_stats создано")

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
        print("  ✅ Представление style_performance создано")

        # Представление для статистики паков
        await db.execute("""
            CREATE VIEW IF NOT EXISTS pack_statistics AS
            SELECT 
                p.id as pack_id,
                p.user_id,
                p.pack_name,
                p.pack_number,
                COUNT(spi.id) as stickers_count,
                p.created_at,
                p.updated_at
            FROM user_sticker_packs p
            LEFT JOIN sticker_pack_items spi ON p.id = spi.pack_id
            GROUP BY p.id
        """)
        print("  ✅ Представление pack_statistics создано")

        # Представление для сводки по пользователям
        await db.execute("""
            CREATE VIEW IF NOT EXISTS user_pack_summary AS
            SELECT 
                u.user_id,
                u.username,
                COUNT(DISTINCT p.id) as total_packs,
                COUNT(DISTINCT spi.sticker_id) as stickers_in_packs,
                MAX(p.updated_at) as last_pack_update
            FROM users u
            LEFT JOIN user_sticker_packs p ON u.user_id = p.user_id
            LEFT JOIN sticker_pack_items spi ON p.id = spi.pack_id
            GROUP BY u.user_id
        """)
        print("  ✅ Представление user_pack_summary создано")

        await db.commit()

        # ========== ПОКАЗЫВАЕМ ИТОГОВУЮ СТРУКТУРУ ==========
        print("\n✅ Миграция завершена успешно!")

        # Показываем структуру основных таблиц
        print("\n📋 Структура обновленных таблиц:")

        tables_to_show = ["stickers", "user_sticker_packs", "sticker_pack_items"]

        for table_name in tables_to_show:
            if await check_table_exists(db, table_name):
                print(f"\n🔹 Таблица {table_name}:")
                cursor = await db.execute(f"PRAGMA table_info({table_name})")
                columns = await cursor.fetchall()
                for col in columns:
                    nullable = "" if col[3] else " NOT NULL"
                    default = f" DEFAULT {col[4]}" if col[4] is not None else ""
                    print(f"  - {col[1]} ({col[2]}{nullable}{default})")

        # Показываем статистику
        print("\n📊 Статистика базы данных:")

        # Количество пользователей
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]
        print(f"  👥 Пользователей: {users_count}")

        # Количество стикеров
        if await check_table_exists(db, "stickers"):
            cursor = await db.execute("SELECT COUNT(*) FROM stickers WHERE is_deleted = 0")
            stickers_count = (await cursor.fetchone())[0]
            print(f"  🎨 Стикеров: {stickers_count}")

        # Количество паков
        if await check_table_exists(db, "user_sticker_packs"):
            cursor = await db.execute("SELECT COUNT(*) FROM user_sticker_packs")
            packs_count = (await cursor.fetchone())[0]
            print(f"  📦 Стикерпаков: {packs_count}")

        print("\n✨ Все готово для работы со стикерпаками!")
        print("💡 Не забудьте обновить config.py с именем вашего бота (BOT_USERNAME)")


if __name__ == "__main__":
    asyncio.run(migrate_database())