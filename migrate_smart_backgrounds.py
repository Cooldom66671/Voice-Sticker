#!/usr/bin/env python3
"""
Скрипт миграции для внедрения умной генерации фонов
"""
import asyncio
import aiosqlite
from pathlib import Path
from datetime import datetime


async def migrate_smart_backgrounds():
    """Выполняет миграцию для умной генерации фонов"""
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
        return

    print(f"🔄 Начинаю миграцию базы данных: {db_path}")
    print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n🎯 Цель: внедрение умной генерации фонов\n")

    async with aiosqlite.connect(db_path) as db:
        # Обновляем существующие записи
        print("🔧 Обновляю типы фонов...")

        # Помечаем все существующие как auto_generated
        await db.execute("""
            UPDATE stickers 
            SET background = 'auto_generated' 
            WHERE background IS NOT NULL
        """)

        print("  ✅ Существующие стикеры обновлены")

        # Создаем новые представления
        print("\n📊 Создаю представления для аналитики...")

        # Удаляем старые представления если есть
        await db.execute("DROP VIEW IF EXISTS generation_type_stats")
        await db.execute("DROP VIEW IF EXISTS style_background_correlation")

        # Статистика по типам генерации
        await db.execute("""
            CREATE VIEW generation_type_stats AS
            SELECT 
                CASE 
                    WHEN background IN ('minimal', 'transparent', 'white') THEN 'without_background'
                    WHEN background IN ('contextual', 'gradient', 'complex') THEN 'with_background'
                    ELSE 'auto_generated'
                END as generation_type,
                COUNT(*) as total_count,
                AVG(rating) as avg_rating,
                COUNT(CASE WHEN rating >= 4 THEN 1 END) * 100.0 / COUNT(rating) as success_rate
            FROM stickers
            WHERE is_deleted = 0
            GROUP BY generation_type
        """)
        print("  ✅ Создано представление generation_type_stats")

        # Корреляция стиля и типа фона
        await db.execute("""
            CREATE VIEW style_background_correlation AS
            SELECT 
                style,
                background,
                COUNT(*) as count,
                AVG(rating) as avg_rating,
                COUNT(CASE WHEN rating >= 4 THEN 1 END) as good_count
            FROM stickers
            WHERE is_deleted = 0 AND rating IS NOT NULL
            GROUP BY style, background
            ORDER BY style, count DESC
        """)
        print("  ✅ Создано представление style_background_correlation")

        # Анализ промптов с локациями
        await db.execute("""
            CREATE VIEW location_prompt_analysis AS
            SELECT 
                COUNT(*) as total,
                COUNT(CASE 
                    WHEN prompt LIKE '%космос%' OR prompt LIKE '%space%' 
                    OR prompt LIKE '%лес%' OR prompt LIKE '%forest%'
                    OR prompt LIKE '%море%' OR prompt LIKE '%sea%'
                    OR prompt LIKE '%город%' OR prompt LIKE '%city%'
                    OR prompt LIKE '%офис%' OR prompt LIKE '%office%'
                    OR prompt LIKE '%парк%' OR prompt LIKE '%park%'
                    THEN 1 
                END) as with_location,
                AVG(CASE 
                    WHEN prompt LIKE '%космос%' OR prompt LIKE '%space%' 
                    OR prompt LIKE '%лес%' OR prompt LIKE '%forest%'
                    OR prompt LIKE '%море%' OR prompt LIKE '%sea%'
                    OR prompt LIKE '%город%' OR prompt LIKE '%city%'
                    OR prompt LIKE '%офис%' OR prompt LIKE '%office%'
                    OR prompt LIKE '%парк%' OR prompt LIKE '%park%'
                    THEN rating 
                END) as location_avg_rating
            FROM stickers
            WHERE is_deleted = 0
        """)
        print("  ✅ Создано представление location_prompt_analysis")

        await db.commit()

        # Показываем статистику
        print("\n📊 Анализ существующих данных:")

        # Анализ промптов
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE 
                    WHEN prompt LIKE '%в %' OR prompt LIKE '%на %' 
                    OR prompt LIKE '%под %' OR prompt LIKE '% in %'
                    OR prompt LIKE '% on %' OR prompt LIKE '% at %'
                    THEN 1 
                END) as possibly_with_location
            FROM stickers
            WHERE is_deleted = 0
        """)
        total, with_location = await cursor.fetchone()

        if total > 0:
            location_percent = (with_location / total * 100) if with_location else 0
            print(f"\n  📝 Анализ промптов:")
            print(f"  • Всего стикеров: {total}")
            print(f"  • Возможно с локацией: {with_location} ({location_percent:.1f}%)")

        # Статистика по стилям
        cursor = await db.execute("""
            SELECT style, COUNT(*) as count, AVG(rating) as avg_rating
            FROM stickers
            WHERE is_deleted = 0
            GROUP BY style
            ORDER BY count DESC
            LIMIT 5
        """)

        styles = await cursor.fetchall()
        if styles:
            print(f"\n  🎨 Топ стилей:")
            for style, count, rating in styles:
                rating_str = f"{rating:.1f}" if rating else "N/A"
                print(f"  • {style}: {count} стикеров (рейтинг: {rating_str})")

        print("\n✨ Миграция завершена!")
        print("\n🚀 Новые возможности:")
        print("  • AI автоматически определяет нужен ли фон")
        print("  • 'кот' → минимальный фон")
        print("  • 'кот в космосе' → космический фон")
        print("  • Улучшенное качество генерации")
        print("  • Автоматическое удаление простых фонов")
        print("\n💡 Обновите код бота для использования новых функций!")


if __name__ == "__main__":
    asyncio.run(migrate_smart_backgrounds())