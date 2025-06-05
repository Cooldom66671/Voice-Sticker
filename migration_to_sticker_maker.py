#!/usr/bin/env python3
"""
Скрипт миграции для перехода на fofr/sticker-maker
Обновляет БД и настройки
"""
import sqlite3
from pathlib import Path
import json
from datetime import datetime

# Путь к БД
DB_PATH = Path("bot_database.db")


def migrate_to_sticker_maker():
    """Выполняет миграцию БД"""
    print("🔄 Начинаю миграцию на sticker-maker...")

    if not DB_PATH.exists():
        print("❌ База данных не найдена!")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Обновляем значения background на 'auto'
        print("\n1️⃣ Обновляю поле background...")
        cursor.execute("""
            UPDATE stickers 
            SET background = 'auto' 
            WHERE background IN ('transparent', 'white', 'gradient', 'pattern')
        """)
        updated = cursor.rowcount
        print(f"   ✅ Обновлено записей: {updated}")

        # 2. Добавляем индекс если его нет
        print("\n2️⃣ Проверяю индексы...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_stickers_model 
            ON stickers(metadata)
        """)
        print("   ✅ Индексы обновлены")

        # 3. Обновляем метаданные для указания модели
        print("\n3️⃣ Обновляю метаданные...")
        cursor.execute("SELECT id, metadata FROM stickers WHERE metadata IS NOT NULL")
        stickers = cursor.fetchall()

        updated_meta = 0
        for sticker_id, metadata_str in stickers:
            try:
                metadata = json.loads(metadata_str) if metadata_str else {}
                if 'model' not in metadata:
                    metadata['model'] = 'sdxl'  # Старые стикеры были на SDXL
                    cursor.execute(
                        "UPDATE stickers SET metadata = ? WHERE id = ?",
                        (json.dumps(metadata), sticker_id)
                    )
                    updated_meta += 1
            except:
                pass

        print(f"   ✅ Обновлено метаданных: {updated_meta}")

        # 4. Создаем представление для аналитики по моделям
        print("\n4️⃣ Создаю представления для аналитики...")
        cursor.execute("DROP VIEW IF EXISTS model_statistics")
        cursor.execute("""
            CREATE VIEW model_statistics AS
            SELECT 
                COALESCE(
                    json_extract(metadata, '$.model'), 
                    'sdxl'
                ) as model,
                COUNT(*) as total_stickers,
                AVG(rating) as avg_rating,
                AVG(json_extract(metadata, '$.generation_time')) as avg_generation_time
            FROM stickers
            WHERE is_deleted = FALSE
            GROUP BY model
        """)
        print("   ✅ Представления созданы")

        # 5. Добавляем настройку модели в user_preferences
        print("\n5️⃣ Обновляю настройки пользователей...")

        # Проверяем существование колонки
        cursor.execute("PRAGMA table_info(user_preferences)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'preferred_model' not in columns:
            cursor.execute("""
                ALTER TABLE user_preferences 
                ADD COLUMN preferred_model TEXT DEFAULT 'sticker-maker'
            """)
            print("   ✅ Добавлено поле preferred_model")

        # Коммитим изменения
        conn.commit()

        # Показываем статистику
        print("\n📊 Статистика после миграции:")
        cursor.execute("SELECT COUNT(*) FROM stickers WHERE is_deleted = FALSE")
        total_stickers = cursor.fetchone()[0]
        print(f"   Всего стикеров: {total_stickers}")

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM stickers")
        total_users = cursor.fetchone()[0]
        print(f"   Активных пользователей: {total_users}")

        cursor.execute("SELECT * FROM model_statistics")
        print("\n   Статистика по моделям:")
        for row in cursor.fetchall():
            model, count, rating, gen_time = row
            print(f"   - {model}: {count} стикеров, рейтинг {rating or 0:.2f}, время {gen_time or 0:.1f}с")

        print("\n✅ Миграция успешно завершена!")
        print("\n🎯 Рекомендации:")
        print("1. Перезапустите бота")
        print("2. Новые стикеры будут создаваться через fofr/sticker-maker")
        print("3. Старые стикеры остаются без изменений")

    except Exception as e:
        print(f"\n❌ Ошибка миграции: {e}")
        conn.rollback()

    finally:
        conn.close()


def create_backup():
    """Создает резервную копию БД"""
    if DB_PATH.exists():
        backup_name = f"bot_database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = DB_PATH.parent / backup_name

        import shutil
        shutil.copy2(DB_PATH, backup_path)
        print(f"📦 Создана резервная копия: {backup_path}")
        return True
    return False


if __name__ == "__main__":
    print("🚀 Миграция на fofr/sticker-maker")
    print("=" * 50)

    # Создаем бэкап
    if create_backup():
        # Выполняем миграцию
        migrate_to_sticker_maker()
    else:
        print("❌ Не удалось создать резервную копию!")