# prompt_optimization/optimizer.py

import json
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import asyncio
from dataclasses import dataclass, asdict
import numpy as np
from pathlib import Path

from logger import logger


@dataclass
class PromptTemplate:
    """Шаблон промпта для тестирования"""
    id: str
    name: str
    system_prompt: str
    example_format: str
    success_rate: float = 0.0
    total_uses: int = 0
    total_rating: int = 0
    active: bool = True

    @property
    def average_rating(self) -> float:
        return self.total_rating / self.total_uses if self.total_uses > 0 else 0.0


class PromptOptimizer:
    """Система оптимизации промптов с A/B тестированием"""

    def __init__(self, config_path: str = "prompt_templates.json"):
        self.config_path = config_path
        self.templates = self._load_templates()
        self.test_results = []
        self.current_champion = None

    def _load_templates(self) -> Dict[str, PromptTemplate]:
        """Загрузка шаблонов промптов"""
        default_templates = {
            "precise_v1": PromptTemplate(
                id="precise_v1",
                name="Precise Description v1",
                system_prompt="""You are an expert AI prompt engineer specializing in sticker generation.
Your task is to create EXACT and DETAILED prompts that precisely match user requests.

CRITICAL RULES:
1. The user's main subject MUST be the central focus
2. Add only complementary details that enhance the main subject
3. Keep descriptions clear and visually specific
4. Maintain sticker-appropriate simplicity""",
                example_format="""Main subject: [exact user request]
Style: [chosen style] sticker art
Details: [3-5 specific visual details]
Background: simple, clean
Quality: high resolution, professional"""
            ),

            "structured_v1": PromptTemplate(
                id="structured_v1",
                name="Structured Components v1",
                system_prompt="""You are a sticker prompt specialist. Break down requests into clear components.

OUTPUT STRUCTURE:
- Subject: [main element]
- Action/Pose: [if applicable]
- Expression/Mood: [emotional state]
- Style Elements: [artistic details]
- Color Scheme: [dominant colors]
- Background: [simple description]""",
                example_format="[Subject] + [action] + [mood] + [style] + sticker design, [background]"
            ),

            "visual_first_v1": PromptTemplate(
                id="visual_first_v1",
                name="Visual-First Approach v1",
                system_prompt="""Focus on creating highly visual, specific prompts for sticker generation.

APPROACH:
1. Start with the most visually distinctive features
2. Use concrete visual adjectives (not abstract concepts)
3. Specify exact colors, shapes, and proportions
4. Describe the subject as if explaining to someone who will draw it""",
                example_format="A [size] [color] [subject] with [distinctive feature], [pose/action], [style] sticker illustration"
            ),

            "minimalist_v1": PromptTemplate(
                id="minimalist_v1",
                name="Minimalist Clear v1",
                system_prompt="""Create simple, clear prompts focusing on essential elements only.

RULES:
- Maximum 15 words per prompt
- Only include crucial visual elements
- One main subject, one action, one style
- Avoid unnecessary adjectives""",
                example_format="[subject] [key characteristic], [style] sticker, simple background"
            ),

            "emotion_focused_v1": PromptTemplate(
                id="emotion_focused_v1",
                name="Emotion-Centric v1",
                system_prompt="""Emphasize emotional expression and mood in sticker generation.

PRIORITY ORDER:
1. Subject identification
2. Emotional state/expression
3. Body language that reinforces emotion
4. Style that complements the mood
5. Colors that enhance the feeling""",
                example_format="[emotion] [subject] showing [expression], [pose], [mood-appropriate style] sticker"
            )
        }

        # Пытаемся загрузить сохраненные шаблоны
        config_file = Path(self.config_path)
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    saved_data = json.load(f)
                    templates = {}
                    for tid, tdata in saved_data.items():
                        templates[tid] = PromptTemplate(**tdata)
                    return templates
            except Exception as e:
                logger.error(f"Ошибка загрузки шаблонов: {e}")

        # Если файл не существует, создаем с дефолтными шаблонами
        logger.info("Создаю новый файл шаблонов промптов")
        self._save_templates(default_templates)
        return default_templates

    def _save_templates(self, templates: Dict[str, PromptTemplate]):
        """Сохранение шаблонов"""
        data = {tid: asdict(t) for tid, t in templates.items()}
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_test_template(self, user_id: int) -> PromptTemplate:
        """Выбор шаблона для A/B тестирования"""
        active_templates = [t for t in self.templates.values() if t.active]

        if not active_templates:
            return list(self.templates.values())[0]

        # Используем Thompson Sampling для выбора шаблона
        scores = []
        for template in active_templates:
            # Для новых шаблонов используем оптимистичный подход
            if template.total_uses == 0:
                # Начальный оптимистичный score для исследования
                score = np.random.beta(1, 1)  # Uniform distribution
            else:
                # Байесовский подход: Beta(успехи + 1, неудачи + 1)
                successes = int(template.success_rate * template.total_uses)
                failures = template.total_uses - successes

                # Добавляем псевдо-наблюдения для стабильности
                alpha = max(1, successes + 1)
                beta_param = max(1, failures + 1)

                score = np.random.beta(alpha, beta_param)

            scores.append((score, template))

        # Выбираем шаблон с наивысшим сэмплированным score
        scores.sort(key=lambda x: x[0], reverse=True)
        selected_template = scores[0][1]

        logger.info(f"Выбран шаблон {selected_template.name} для пользователя {user_id} (score: {scores[0][0]:.3f})")
        return selected_template

    def record_result(self, template_id: str, rating: int, request: str):
        """Запись результата использования шаблона"""
        if template_id not in self.templates:
            return

        template = self.templates[template_id]
        template.total_uses += 1
        template.total_rating += rating

        # Обновляем success rate (оценка 4-5 считается успехом)
        is_success = rating >= 4
        template.success_rate = (
                (template.success_rate * (template.total_uses - 1) + (1 if is_success else 0))
                / template.total_uses
        )

        # Сохраняем результат для анализа
        self.test_results.append({
            "timestamp": datetime.now().isoformat(),
            "template_id": template_id,
            "rating": rating,
            "request": request,
            "is_success": is_success
        })

        # Сохраняем обновлённые шаблоны
        self._save_templates(self.templates)

        # Проверяем, нужно ли обновить чемпиона
        self._update_champion()

        logger.info(
            f"Результат записан: template={template_id}, rating={rating}, success_rate={template.success_rate:.2%}")

    def _update_champion(self):
        """Обновление лучшего шаблона"""
        # Требуем минимум 20 использований для надёжной статистики
        eligible_templates = [
            t for t in self.templates.values()
            if t.total_uses >= 20 and t.active
        ]

        if not eligible_templates:
            return

        # Выбираем по наивысшему success rate с учётом средней оценки
        best_template = max(
            eligible_templates,
            key=lambda t: t.success_rate * 0.7 + (t.average_rating / 5.0) * 0.3
        )

        if self.current_champion != best_template.id:
            self.current_champion = best_template.id
            logger.info(f"Новый чемпион: {best_template.name} "
                        f"(success: {best_template.success_rate:.2%}, "
                        f"avg rating: {best_template.average_rating:.2f})")

    def get_statistics(self) -> Dict:
        """Получение статистики по шаблонам"""
        stats = {
            "templates": {},
            "current_champion": self.current_champion,
            "total_tests": len(self.test_results)
        }

        for tid, template in self.templates.items():
            stats["templates"][tid] = {
                "name": template.name,
                "uses": template.total_uses,
                "success_rate": f"{template.success_rate:.1%}",
                "avg_rating": f"{template.average_rating:.2f}",
                "active": template.active
            }

        # Анализ последних 7 дней
        recent_results = [
            r for r in self.test_results
            if datetime.fromisoformat(r["timestamp"]) > datetime.now() - timedelta(days=7)
        ]

        if recent_results:
            stats["recent_7_days"] = {
                "total_tests": len(recent_results),
                "avg_rating": sum(r["rating"] for r in recent_results) / len(recent_results),
                "success_rate": sum(1 for r in recent_results if r["is_success"]) / len(recent_results)
            }

        return stats

    def add_custom_template(self, template: PromptTemplate):
        """Добавление пользовательского шаблона"""
        self.templates[template.id] = template
        self._save_templates(self.templates)
        logger.info(f"Добавлен новый шаблон: {template.name}")

    def deactivate_poor_performers(self, min_uses: int = 50, success_threshold: float = 0.3):
        """Деактивация плохо работающих шаблонов"""
        deactivated = []
        for template in self.templates.values():
            if template.total_uses >= min_uses and template.success_rate < success_threshold:
                template.active = False
                deactivated.append(template.name)
                logger.info(f"Деактивирован шаблон {template.name} "
                            f"(success rate: {template.success_rate:.1%})")

        if deactivated:
            self._save_templates(self.templates)

        return deactivated

    def export_best_practices(self) -> List[Dict]:
        """Экспорт лучших практик на основе результатов"""
        # Анализируем успешные генерации
        successful_results = [r for r in self.test_results if r["is_success"]]

        # Группируем по шаблонам
        template_examples = {}
        for result in successful_results:
            tid = result["template_id"]
            if tid not in template_examples:
                template_examples[tid] = []
            template_examples[tid].append({
                "request": result["request"],
                "rating": result["rating"]
            })

        # Выбираем лучшие примеры для каждого шаблона
        best_practices = []
        for tid, examples in template_examples.items():
            if tid not in self.templates:
                continue

            # Сортируем по рейтингу и берём топ-5
            examples.sort(key=lambda x: x["rating"], reverse=True)
            top_examples = examples[:5]

            best_practices.append({
                "template": self.templates[tid].name,
                "success_rate": self.templates[tid].success_rate,
                "avg_rating": self.templates[tid].average_rating,
                "examples": top_examples
            })

        # Сортируем по success rate
        best_practices.sort(key=lambda x: x["success_rate"], reverse=True)

        return best_practices

    def get_champion_template(self) -> Optional[PromptTemplate]:
        """Получить текущий лучший шаблон"""
        if self.current_champion and self.current_champion in self.templates:
            return self.templates[self.current_champion]
        return None

    async def run_periodic_optimization(self, interval_hours: int = 24):
        """Периодическая оптимизация (запускать в фоне)"""
        while True:
            try:
                # Деактивируем плохие шаблоны
                deactivated = self.deactivate_poor_performers()

                if deactivated:
                    logger.info(f"Деактивировано {len(deactivated)} плохих шаблонов")

                # Экспортируем лучшие практики
                best_practices = self.export_best_practices()

                # Сохраняем в файл
                with open('best_practices.json', 'w', encoding='utf-8') as f:
                    json.dump(best_practices, f, indent=2, ensure_ascii=False)

                logger.info(f"Экспортировано {len(best_practices)} лучших практик")

                # Ждем следующий цикл
                await asyncio.sleep(interval_hours * 3600)

            except Exception as e:
                logger.error(f"Ошибка в периодической оптимизации: {e}")
                await asyncio.sleep(3600)  # Ждем час при ошибке


# Создаем глобальный экземпляр оптимизатора
prompt_optimizer = PromptOptimizer()

# Экспортируем для использования
__all__ = ["prompt_optimizer", "PromptOptimizer", "PromptTemplate"]