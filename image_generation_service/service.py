"""
Сервис генерации изображений для Telegram стикеров
Оптимизирован для использования fofr/sticker-maker
"""
import os
import asyncio
import aiohttp
import replicate
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import json
import re

import google.generativeai as genai
from logger import logger, log_function

# Настройки
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модели на Replicate
MODELS = {
    "sticker-maker": {
        "id": "fofr/sticker-maker:4acb778eb059772225ec213948f0660867b2e03f277448f18cf1800b96a65a1a",
        "name": "Sticker Maker (Рекомендуется)",
        "description": "Специализированная модель для стикеров"
    },
    "sdxl-lightning": {
        "id": "bytedance/sdxl-lightning-4step:5f24084160c9089501c1b3545d9be3c27883ae2239b6f412990e82d4a6210f8f",
        "name": "SDXL Lightning (Быстрая)",
        "description": "Очень быстрая генерация"
    },
    "flux": {
        "id": "black-forest-labs/flux-schnell",
        "name": "Flux (Максимальная точность)",
        "description": "Лучшее следование промптам"
    }
}

# Стили для стикеров
STYLE_PROMPTS = {
    "cartoon": {
        "positive": "cartoon style, flat colors, simple design, cute, kawaii",
        "negative": "realistic, complex, detailed, photographic"
    },
    "anime": {
        "positive": "anime style, manga, japanese animation, expressive",
        "negative": "western cartoon, realistic, 3d render"
    },
    "realistic": {
        "positive": "photorealistic, detailed, high quality",
        "negative": "cartoon, anime, illustration, drawing"
    },
    "pixel": {
        "positive": "pixel art, 8-bit, retro game style, pixelated",
        "negative": "smooth, realistic, high resolution"
    },
    "minimalist": {
        "positive": "minimalist, simple shapes, flat design, clean",
        "negative": "complex, detailed, realistic, busy"
    },
    "cute": {
        "positive": "kawaii, chibi, adorable, big eyes, cute style",
        "negative": "scary, realistic, dark, serious"
    }
}

# Параметры для разных моделей
MODEL_PARAMS = {
    "sticker-maker": {
        "width": 512,
        "height": 512,
        "num_outputs": 1,
        "output_format": "png",
        "remove_background": True,
        "steps": 20,
        "guidance": 7.5
    },
    "sdxl-lightning": {
        "width": 1024,
        "height": 1024,
        "num_outputs": 1,
        "output_format": "png"
    },
    "flux": {
        "aspect_ratio": "1:1",
        "output_format": "png",
        "go_fast": True,
        "num_inference_steps": 4
    }
}


class ImageGenerationService:
    """Сервис для генерации изображений стикеров"""

    def __init__(self):
        self.replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        self.current_model = "sticker-maker"  # По умолчанию используем sticker-maker

        # Инициализация Gemini для улучшения промптов
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            self.gemini_model = genai.GenerativeModel('gemini-1.5-flash') # ИЗМЕНЕНИЕ: Модель Gemini
            self.use_gemini = True
        else:
            self.use_gemini = False
            logger.warning("Gemini API key not found, using basic prompt enhancement")

    @log_function
    async def enhance_prompt(self, prompt: str, style: str = "cartoon") -> Tuple[str, bool]:
        """
        Улучшает промпт для лучшей генерации стикера
        Returns: (enhanced_prompt, needs_background)
        """
        # Определяем нужен ли фон
        needs_background = self._check_if_background_needed(prompt)

        # Базовое улучшение промпта
        base_enhancement = self._enhance_prompt_basic(prompt, style, needs_background)

        # Если Gemini доступен, используем его для дополнительного улучшения
        if self.use_gemini and style in ["realistic", "anime"]:
            try:
                enhanced = await self._enhance_with_gemini(prompt, style, needs_background)
                return enhanced, needs_background
            except Exception as e:
                logger.warning(f"Gemini enhancement failed, using basic: {e}")

        return base_enhancement, needs_background

    def _check_if_background_needed(self, prompt: str) -> bool:
        """Определяет нужен ли фон на основе анализа промпта"""
        prompt_lower = prompt.lower()

        # Паттерны указывающие на необходимость фона
        location_indicators = [
            'в ', 'на ', 'под ', 'около ', 'у ', 'возле ',
            'in ', 'on ', 'at ', 'near ', 'by ', 'under '
        ]

        # Конкретные локации
        locations = [
            'космос', 'лес', 'море', 'город', 'дом', 'офис', 'парк',
            'горы', 'пустыня', 'пляж', 'улица', 'комната', 'кухня',
            'space', 'forest', 'ocean', 'city', 'house', 'office', 'park'
        ]

        # Проверяем наличие индикаторов локации
        for indicator in location_indicators:
            if indicator in prompt_lower:
                for location in locations:
                    if location in prompt_lower:
                        logger.info(f"Background needed for prompt: {prompt}")
                        return True

        return False

    def _enhance_prompt_basic(self, prompt: str, style: str, needs_background: bool) -> str:
        """Базовое улучшение промпта"""
        # Добавляем стиль
        style_addition = STYLE_PROMPTS.get(style, STYLE_PROMPTS["cartoon"])["positive"]

        # Формируем промпт в зависимости от необходимости фона
        if needs_background:
            # С фоном - добавляем детали окружения
            enhanced = f"{prompt}, {style_addition}, detailed environment, sticker design"
        else:
            # Без фона - изолированный объект
            enhanced = f"{prompt}, {style_addition}, white background, isolated character, centered, sticker"

        # Добавляем универсальные улучшения
        enhanced += ", high quality, clear details"

        return enhanced

    async def _enhance_with_gemini(self, prompt: str, style: str, needs_background: bool) -> str:
        """Улучшение промпта с помощью Gemini"""
        try:
            background_instruction = (
                "with detailed environment and background" if needs_background
                else "on white background, isolated object, no environment"
            )

            system_prompt = f"""
            Enhance this prompt for a Telegram sticker in {style} style.
            Original: "{prompt}"

            Requirements:
            - Make it more descriptive and specific
            - Keep the main subject unchanged
            - {background_instruction}
            - Optimize for sticker format (centered, clear, expressive)
            - Add style-specific details

            Return only the enhanced prompt, nothing else.
            """

            # Gemini generate_content - синхронный метод
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.gemini_model.generate_content,
                system_prompt
            )

            enhanced = response.text.strip()

            # Убираем кавычки если есть
            enhanced = enhanced.strip('"').strip("'")

            logger.info(f"Gemini enhanced: {prompt} -> {enhanced}")
            return enhanced

        except Exception as e:
            logger.error(f"Gemini enhancement error: {e}")
            return self._enhance_prompt_basic(prompt, style, needs_background)

    @log_function
    async def generate_sticker_with_validation(
            self,
            prompt: str,
            style: str = "cartoon",
            model_name: str = None,
            **kwargs
    ) -> Tuple[Optional[bytes], Dict[str, Any]]:
        """
        Генерирует стикер с валидацией и метаданными

        Args:
            prompt: Текст запроса
            style: Стиль генерации
            model_name: Название модели (если None, используется текущая)

        Returns:
            (image_bytes, metadata)
        """
        start_time = datetime.now()
        model_name = model_name or self.current_model

        try:
            # Улучшаем промпт
            enhanced_prompt, needs_background = await self.enhance_prompt(prompt, style)

            # Получаем параметры модели
            model_config = MODELS.get(model_name, MODELS["sticker-maker"])
            params = MODEL_PARAMS.get(model_name, MODEL_PARAMS["sticker-maker"]).copy()

            # Добавляем промпт и negative prompt
            params["prompt"] = enhanced_prompt

            # Для sticker-maker добавляем специфичные параметры
            if model_name == "sticker-maker":
                params["negative_prompt"] = STYLE_PROMPTS.get(style, STYLE_PROMPTS["cartoon"])["negative"]
                params["guidance_scale"] = params.pop("guidance", 7.5)
                params["num_inference_steps"] = params.pop("steps", 20)

            # Для Flux используем другой формат параметров
            elif model_name == "flux":
                params = {
                    "prompt": enhanced_prompt,
                    "aspect_ratio": params.get("aspect_ratio", "1:1"),
                    "output_format": params.get("output_format", "png"),
                    "go_fast": params.get("go_fast", True),
                    "num_inference_steps": params.get("num_inference_steps", 4)
                }

            logger.info(f"Generating with {model_name}: {enhanced_prompt[:100]}...")
            logger.info(f"Style: {style}, Background needed: {needs_background}")

            # Генерация через Replicate
            output = await asyncio.to_thread(
                self.replicate_client.run,
                model_config["id"],
                input=params
            )

            # Получаем URL изображения
            if isinstance(output, list) and len(output) > 0:
                image_url = output[0]
            else:
                image_url = str(output)

            # Скачиваем изображение
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()

                        generation_time = (datetime.now() - start_time).total_seconds()

                        metadata = {
                            "prompt": prompt,
                            "enhanced_prompt": enhanced_prompt,
                            "style": style,
                            "model": model_name,
                            "generation_time": generation_time,
                            "image_url": image_url,
                            "needs_background": needs_background,
                            "timestamp": datetime.now().isoformat()
                        }

                        logger.info(f"Successfully generated sticker in {generation_time:.2f}s")
                        return image_data, metadata
                    else:
                        logger.error(f"Failed to download image: {response.status}")
                        return None, {"error": f"Download failed: {response.status}"}

        except Exception as e:
            logger.error(f"Generation error: {str(e)}")
            return None, {"error": str(e)}

    def set_model(self, model_name: str):
        """Переключает текущую модель"""
        if model_name in MODELS:
            self.current_model = model_name
            logger.info(f"Switched to model: {model_name}")
        else:
            logger.warning(f"Unknown model: {model_name}, keeping {self.current_model}")

    def get_available_models(self) -> Dict[str, Any]:
        """Возвращает список доступных моделей"""
        return MODELS

    def get_current_model(self) -> str:
        """Возвращает текущую модель"""
        return self.current_model


# Создаем единственный экземпляр сервиса
image_service = ImageGenerationService()

# Экспортируем для удобства
__all__ = ["image_service", "ImageGenerationService", "STYLE_PROMPTS"]