"""
Сервис генерации изображений для бота VoiceSticker
Версия с улучшенной точностью, обратной связью и A/B тестированием
"""
from PIL import Image, ImageDraw, ImageFont
from typing import Optional, Dict, Any, Tuple, List
import random
import io
import json
from datetime import datetime
import aiohttp
import asyncio

from logger import logger, log_function
from config import (
    MVP_MODE, IMAGE_SIZE,
    SD_DEVICE, IMAGE_MODEL,
    REPLICATE_API_TOKEN
)

# Импорт системы A/B тестирования
try:
    from prompt_optimization import prompt_optimizer

    AB_TESTING_AVAILABLE = True
except ImportError:
    AB_TESTING_AVAILABLE = False
    prompt_optimizer = None
    logger.warning("prompt_optimization не установлен, A/B тестирование отключено")

# Импорты для перевода и AI
try:
    from deep_translator import GoogleTranslator

    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
    logger.warning("deep-translator не установлен")

try:
    import google.generativeai as genai
    from config import GEMINI_API_KEY

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    GEMINI_API_KEY = None
    logger.warning("google-generativeai не установлен")


class ImageGenerationService:
    """Сервис для генерации изображений с улучшенной точностью и A/B тестированием"""

    def __init__(self):
        self.is_mvp = MVP_MODE
        self.device = SD_DEVICE
        self.model_name = IMAGE_MODEL
        self.model = None
        self.is_ready = False

        # Кэши для оптимизации
        self.translation_cache = {}
        self.prompt_cache = {}

        # Статистика для анализа
        self.generation_stats = {
            'total': 0,
            'successful': 0,
            'failed': 0,
            'feedback': [],
            'style_performance': {}
        }

        # Инициализация Gemini с оптимальными настройками
        if GEMINI_AVAILABLE and GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            self.gemini_model = genai.GenerativeModel(
                'gemini-1.5-flash',
                generation_config=genai.GenerationConfig(
                    temperature=0.7,  # Баланс между креативностью и точностью
                    top_p=0.9,
                    top_k=40,
                    max_output_tokens=1024,
                )
            )
        else:
            self.gemini_model = None

        logger.info(
            f"Инициализация сервиса генерации изображений (MVP mode: {self.is_mvp}, A/B testing: {AB_TESTING_AVAILABLE})")

    async def initialize(self):
        """Инициализация сервиса"""
        if self.is_mvp:
            logger.info("Работаем в MVP режиме - используем заглушки для изображений")
            self.is_ready = True
        else:
            logger.info("Инициализация сервиса генерации через Replicate API")
            self.is_ready = True

    async def translate_to_english(self, text: str) -> Tuple[str, str]:
        """
        Переводит текст на английский с определением языка

        Returns:
            Tuple[translated_text, source_language]
        """
        # Проверяем кэш
        if text in self.translation_cache:
            return self.translation_cache[text]

        try:
            if TRANSLATOR_AVAILABLE:
                translator = GoogleTranslator(source='auto', target='en')

                # Переводим текст
                translated = translator.translate(text)

                # Определяем язык простым способом
                # Если текст не изменился после перевода, значит был английский
                if translated.lower() == text.lower():
                    detected_lang = 'en'
                else:
                    # Пытаемся определить по алфавиту
                    if any('\u0400' <= char <= '\u04FF' for char in text):
                        detected_lang = 'ru'  # Кириллица
                    elif any('\u4e00' <= char <= '\u9fff' for char in text):
                        detected_lang = 'zh'  # Китайский
                    elif any('\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff' for char in text):
                        detected_lang = 'ja'  # Японский
                    else:
                        detected_lang = 'auto'

                result = (translated, detected_lang)

                # Кэшируем результат
                self.translation_cache[text] = result

                logger.info(f"Перевод: '{text}' ({detected_lang}) -> '{translated}'")
                return result

        except Exception as e:
            logger.error(f"Ошибка перевода: {e}")

        # Fallback
        return (text, 'unknown')

    def _generate_negative_prompt(self, style: str) -> str:
        """Генерирует negative prompt для конкретного стиля"""
        base_negative = "ugly, deformed, blurry, bad anatomy, bad proportions, extra limbs, cloned face, disfigured, gross proportions, malformed limbs, missing arms, missing legs, extra arms, extra legs, mutated hands, fused fingers, too many fingers, long neck, watermark, text, error, cropped, worst quality, low quality, jpeg artifacts, signature, username, artist name"

        style_specific = {
            "default": ", realistic photo, complex background",
            "cartoon": ", realistic, photo, anime, complex details",
            "anime": ", realistic, photo, western cartoon, 3d render",
            "minimalist": ", complex, detailed, busy, cluttered",
            "pixel": ", smooth, high resolution, realistic",
            "3d": ", 2d, flat, cartoon, anime",
            "watercolor": ", digital art, 3d, photorealistic",
            "realistic": ", cartoon, anime, illustration, drawing"
        }

        return base_negative + style_specific.get(style, "")

    def _get_optimal_generation_params(self, style: str) -> Dict[str, Any]:
        """Возвращает оптимальные параметры генерации для стиля"""
        base_params = {
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 8.0,
            "scheduler": "DPMSolverMultistep",
            "refine": "expert_ensemble_refiner",
            "high_noise_frac": 0.8,
            "prompt_strength": 0.9,
            "num_outputs": 1
        }

        # Стиль-специфичные параметры
        style_params = {
            "cartoon": {
                "guidance_scale": 7.5,
                "num_inference_steps": 25,
                "scheduler": "K_EULER"
            },
            "anime": {
                "guidance_scale": 9.0,
                "num_inference_steps": 35,
                "high_noise_frac": 0.85
            },
            "realistic": {
                "guidance_scale": 7.0,
                "num_inference_steps": 40,
                "high_noise_frac": 0.7,
                "refine": "expert_ensemble_refiner"
            },
            "minimalist": {
                "guidance_scale": 6.0,
                "num_inference_steps": 20,
                "scheduler": "DDIM"
            },
            "pixel": {
                "guidance_scale": 8.5,
                "num_inference_steps": 25,
                "scheduler": "K_EULER_ANCESTRAL"
            },
            "watercolor": {
                "guidance_scale": 7.0,
                "num_inference_steps": 30,
                "high_noise_frac": 0.75
            },
            "3d": {
                "guidance_scale": 8.0,
                "num_inference_steps": 35,
                "scheduler": "DPMSolverMultistep"
            }
        }

        # Обновляем базовые параметры стиль-специфичными
        if style in style_params:
            base_params.update(style_params[style])

        return base_params

    async def generate_enhanced_prompt(self, user_request: str, style: str, user_id: Optional[int] = None) -> Dict[
        str, str]:
        """
        Генерирует улучшенный промпт с использованием Gemini и A/B тестирования

        Args:
            user_request: Запрос пользователя
            style: Стиль стикера
            user_id: ID пользователя для A/B тестирования

        Returns:
            Dict с полным промптом и метаданными
        """
        # Проверяем кэш
        cache_key = f"{user_request}_{style}"
        if cache_key in self.prompt_cache:
            cached = self.prompt_cache[cache_key].copy()
            # Добавляем информацию о кэше
            cached["_from_cache"] = True
            return cached

        # Переводим запрос
        translated_request, source_lang = await self.translate_to_english(user_request)

        if not self.gemini_model:
            # Fallback без Gemini
            simple_prompt = f"cute sticker of {translated_request}, {style} style, die-cut sticker, white background, centered, high quality"
            return {
                "main_subject": translated_request,
                "full_prompt": simple_prompt,
                "style": style,
                "source_language": source_lang,
                "enhanced": False,
                "_template_id": "fallback",
                "_template_name": "Fallback (No Gemini)"
            }

        try:
            # Детальные описания стилей
            style_guides = {
                "cartoon": "Disney Pixar 3D style with big expressive eyes, smooth rounded shapes, vibrant colors, cute proportions",
                "anime": "Japanese anime/manga chibi style with huge sparkly eyes, small mouth, kawaii aesthetic, pastel colors",
                "realistic": "photorealistic detailed rendering with accurate proportions, natural lighting, high detail textures",
                "minimalist": "ultra simple flat design, geometric shapes, limited color palette, clean lines, no details",
                "pixel": "16-bit retro pixel art style, blocky shapes, limited color palette, nostalgic video game aesthetic",
                "watercolor": "soft watercolor painting, flowing colors, artistic brush strokes, gentle color bleeding",
                "3d": "3D rendered isometric style, soft shadows, ambient occlusion, clay/plastic material look"
            }

            style_description = style_guides.get(style, "high quality sticker style")

            # Получаем шаблон для A/B тестирования
            if AB_TESTING_AVAILABLE and prompt_optimizer:
                template = prompt_optimizer.get_test_template(user_id or 0)
                template_system_prompt = template.system_prompt
                template_format = template.example_format
                template_id = template.id
                template_name = template.name
            else:
                # Дефолтный шаблон если A/B тестирование недоступно
                template_system_prompt = """You are an expert AI prompt engineer specializing in sticker image generation.
Your task is to create PRECISE and DETAILED prompts that exactly match user requests.

CRITICAL RULES:
1. The user's main subject MUST be the central focus
2. NEVER change or reinterpret the main subject - keep it EXACTLY as requested
3. Add only visual enhancements that support the main subject
4. Be specific about colors, expressions, poses, and details
5. Keep the style consistent"""

                template_format = """Main subject: [exact user request]
Style: [chosen style] sticker art
Details: [3-5 specific visual details]
Background: simple, clean
Quality: high resolution, professional"""

                template_id = "default"
                template_name = "Default Template"

            # Комбинируем шаблон с информацией о запросе
            system_prompt = f"""{template_system_prompt}

User request: "{user_request}" (translated: "{translated_request}")
Style: {style_description}
Format: {template_format}

RESPONSE FORMAT (JSON):
{{
    "main_subject": "exact thing user asked for",
    "visual_details": "specific visual description with colors, pose, expression",
    "style_elements": "style-specific visual elements",
    "composition": "how the subject is positioned",
    "mood": "emotional tone or feeling"
}}

Remember: The user asked for "{user_request}" - this MUST be the main focus!"""

            # Генерируем промпт через Gemini
            response = self.gemini_model.generate_content(system_prompt)

            # Парсим JSON ответ
            response_text = response.text.strip()
            if response_text.startswith('```json'):
                response_text = response_text[7:-3]
            elif response_text.startswith('```'):
                response_text = response_text[3:-3]

            prompt_data = json.loads(response_text)

            # Собираем финальный промпт
            full_prompt = (
                f"{prompt_data['main_subject']}, "
                f"{prompt_data['visual_details']}, "
                f"{style_description}, "
                f"{prompt_data['style_elements']}, "
                f"{prompt_data['composition']}, "
                f"{prompt_data['mood']} mood, "
                f"sticker design, die-cut sticker, white background, centered composition, high quality, sharp focus"
            )

            result = {
                "main_subject": prompt_data['main_subject'],
                "full_prompt": full_prompt,
                "visual_details": prompt_data.get('visual_details', ''),
                "style": style,
                "style_description": style_description,
                "source_language": source_lang,
                "original_request": user_request,
                "translated_request": translated_request,
                "enhanced": True,
                "timestamp": datetime.now().isoformat(),
                "_template_id": template_id,
                "_template_name": template_name
            }

            # Кэшируем результат
            self.prompt_cache[cache_key] = result

            logger.info(f"Enhanced prompt created with template {template_name}: {result['main_subject']}")
            return result

        except Exception as e:
            logger.error(f"Ошибка создания enhanced prompt: {e}")
            # Fallback
            simple_prompt = f"cute sticker of {translated_request}, {style} style, die-cut sticker, white background"
            return {
                "main_subject": translated_request,
                "full_prompt": simple_prompt,
                "style": style,
                "source_language": source_lang,
                "enhanced": False,
                "_template_id": "error_fallback",
                "_template_name": "Error Fallback"
            }

    @log_function
    async def generate_sticker_with_validation(self, prompt: str, style: str = 'cartoon',
                                               user_id: Optional[int] = None) -> Tuple[Optional[bytes], Dict]:
        """
        Генерирует стикер с валидацией и метаданными

        Args:
            prompt: Текстовый запрос
            style: Стиль стикера
            user_id: ID пользователя для A/B тестирования

        Returns:
            Tuple[image_bytes, metadata_dict]
        """
        if not self.is_ready:
            await self.initialize()

        start_time = datetime.now()

        try:
            # Генерируем улучшенный промпт с A/B тестированием
            prompt_data = await self.generate_enhanced_prompt(prompt, style, user_id)

            # Генерируем изображение
            if self.is_mvp:
                image = await self._generate_mvp_sticker(prompt, style)
            else:
                image = await self._generate_sd_sticker_enhanced(prompt_data, style)

            if image:
                # Конвертируем в байты
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format='PNG', optimize=True)
                img_byte_arr.seek(0)
                image_bytes = img_byte_arr.getvalue()

                # Обновляем статистику
                self.generation_stats['total'] += 1
                self.generation_stats['successful'] += 1

                # Метаданные
                metadata = {
                    **prompt_data,
                    "generation_time": (datetime.now() - start_time).total_seconds(),
                    "success": True,
                    "image_size": len(image_bytes),
                    "generation_params": self._get_optimal_generation_params(style),
                    "user_id": user_id
                }

                return image_bytes, metadata
            else:
                self.generation_stats['failed'] += 1
                return None, {"success": False, "error": "Failed to generate image"}

        except Exception as e:
            logger.error(f"Ошибка генерации: {e}")
            self.generation_stats['failed'] += 1
            return None, {"success": False, "error": str(e)}

    async def _generate_sd_sticker_enhanced(self, prompt_data: Dict, style: str) -> Optional[Image.Image]:
        """Улучшенная генерация через Replicate с оптимальными параметрами"""
        try:
            import os
            os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

            import replicate

            # Получаем оптимальные параметры
            params = self._get_optimal_generation_params(style)

            # Используем SDXL для максимального качества
            model = "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"

            # Добавляем промпт и negative prompt
            params.update({
                "prompt": prompt_data['full_prompt'],
                "negative_prompt": self._generate_negative_prompt(style)
            })

            logger.info(f"Генерация с параметрами: prompt='{params['prompt'][:100]}...', style={style}")

            # Запускаем генерацию
            output = replicate.run(model, input=params)

            # Преобразуем output в список
            if not isinstance(output, list):
                output = list(output)

            if output and len(output) > 0:
                image_url = str(output[0])
                logger.info(f"Получен URL изображения: {image_url}")

                # Загружаем изображение
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as response:
                        if response.status == 200:
                            image_data = await response.read()
                            image = Image.open(io.BytesIO(image_data))

                            # Изменяем размер для стикера
                            image.thumbnail((512, 512), Image.Resampling.LANCZOS)

                            logger.info("Изображение успешно сгенерировано")
                            return image
                        else:
                            logger.error(f"Ошибка загрузки изображения: {response.status}")

            return None

        except Exception as e:
            logger.error(f"Ошибка при генерации через Replicate: {e}")
            # Fallback на MVP
            return await self._generate_mvp_sticker(prompt_data.get('original_request', 'error'), style)

    async def _generate_mvp_sticker(self, prompt: str, style: str) -> Image.Image:
        """Генерирует заглушку стикера для MVP версии"""
        width, height = IMAGE_SIZE

        # Выбираем цвета в зависимости от стиля
        style_colors = {
            'cartoon': {'bg': (255, 250, 200), 'text': (255, 100, 0), 'accent': (255, 200, 0)},
            'anime': {'bg': (240, 240, 255), 'text': (255, 100, 150), 'accent': (255, 180, 200)},
            'realistic': {'bg': (240, 240, 240), 'text': (60, 60, 60), 'accent': (150, 150, 150)},
            'minimalist': {'bg': (250, 250, 250), 'text': (50, 50, 50), 'accent': (200, 200, 200)},
            'pixel': {'bg': (200, 255, 200), 'text': (0, 150, 0), 'accent': (100, 200, 100)},
            'watercolor': {'bg': (255, 240, 230), 'text': (100, 80, 60), 'accent': (200, 150, 100)},
            '3d': {'bg': (230, 230, 240), 'text': (50, 50, 100), 'accent': (100, 100, 200)}
        }

        colors = style_colors.get(style, {'bg': (255, 240, 245), 'text': (100, 50, 150), 'accent': (255, 200, 220)})

        # Создаем изображение
        img = Image.new('RGBA', (width, height), colors['bg'] + (255,))
        draw = ImageDraw.Draw(img)

        # Добавляем стилизованные элементы
        self._add_style_elements(draw, width, height, style, colors)

        # Добавляем текст
        text_lines = self._wrap_text(prompt, 30)

        try:
            # Попробуем разные пути к шрифтам в зависимости от ОС
            font = None
            small_font = None

            # Список возможных путей к шрифтам
            font_paths = [
                # Mac
                "/System/Library/Fonts/Helvetica.ttc",
                # Windows
                "C:/Windows/Fonts/arial.ttf",
                "arial.ttf",
                # Linux
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ]

            for font_path in font_paths:
                try:
                    font = ImageFont.truetype(font_path, 36)
                    small_font = ImageFont.truetype(font_path, 14)
                    break
                except:
                    continue

            if not font:
                font = ImageFont.load_default()
                small_font = font

        except Exception as e:
            logger.warning(f"Не удалось загрузить шрифт: {e}")
            font = ImageFont.load_default()
            small_font = font

        # Основной текст
        y_offset = height // 2 - (len(text_lines) * 45) // 2
        for line in text_lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2

            # Тень текста
            draw.text((x + 2, y_offset + 2), line, fill=(0, 0, 0, 50), font=font)
            # Основной текст
            draw.text((x, y_offset), line, fill=colors['text'], font=font)
            y_offset += 45

        # Метка MVP
        draw.text((10, height - 25), f"MVP Demo • {style}", fill=(180, 180, 180), font=small_font)

        return img

    def _add_style_elements(self, draw: ImageDraw, width: int, height: int, style: str, colors: Dict):
        """Добавляет декоративные элементы в зависимости от стиля"""
        if style == 'cartoon':
            # Звездочки
            for _ in range(5):
                x = random.randint(50, width - 50)
                y = random.randint(50, height - 50)
                size = random.randint(15, 30)
                self._draw_star(draw, x, y, size, colors['accent'] + (100,))

        elif style == 'anime':
            # Сердечки и блестки
            for _ in range(3):
                x = random.randint(50, width - 50)
                y = random.randint(50, height - 50)
                self._draw_heart(draw, x, y, 25, colors['accent'])

        elif style == 'minimalist':
            # Простые геометрические формы
            draw.rectangle([20, 20, width - 20, height - 20], outline=colors['accent'], width=3)

        elif style == 'pixel':
            # Пиксельная рамка
            pixel_size = 10
            for i in range(0, width, pixel_size * 2):
                draw.rectangle([i, 0, i + pixel_size, pixel_size], fill=colors['accent'])
                draw.rectangle([i, height - pixel_size, i + pixel_size, height], fill=colors['accent'])

    def _wrap_text(self, text: str, max_chars: int) -> List[str]:
        """Разбивает текст на строки"""
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            if len(' '.join(current_line + [word])) <= max_chars:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]

        if current_line:
            lines.append(' '.join(current_line))

        return lines[:3]

    def _draw_star(self, draw: ImageDraw, x: int, y: int, size: int, color: tuple):
        """Рисует звездочку"""
        import math
        points = []
        for i in range(10):
            angle = i * 36 * math.pi / 180
            if i % 2 == 0:
                r = size
            else:
                r = size * 0.5
            px = x + r * math.cos(angle - math.pi / 2)
            py = y + r * math.sin(angle - math.pi / 2)
            points.append((px, py))

        draw.polygon(points, fill=color)

    def _draw_heart(self, draw: ImageDraw, x: int, y: int, size: int, color: tuple):
        """Рисует сердечко"""
        # Верхние круги
        r = size // 3
        draw.ellipse([x - r, y - r // 2, x + r, y + r + r // 2], fill=color)
        draw.ellipse([x, y - r // 2, x + 2 * r, y + r + r // 2], fill=color)

        # Нижний треугольник
        draw.polygon([
            (x - r, y + r // 2),
            (x + 2 * r, y + r // 2),
            (x + r // 2, y + size)
        ], fill=color)

    def record_feedback(self, metadata: Dict, rating: int):
        """Записывает обратную связь для анализа и улучшения"""
        feedback_entry = {
            "timestamp": datetime.now().isoformat(),
            "rating": rating,
            "prompt": metadata.get("original_request", ""),
            "translated": metadata.get("translated_request", ""),
            "style": metadata.get("style", ""),
            "enhanced": metadata.get("enhanced", False),
            "generation_time": metadata.get("generation_time", 0),
            "template_id": metadata.get("_template_id", "unknown"),
            "template_name": metadata.get("_template_name", "Unknown")
        }

        self.generation_stats['feedback'].append(feedback_entry)

        # Обновляем статистику по стилям
        style = metadata.get("style", "unknown")
        if style not in self.generation_stats['style_performance']:
            self.generation_stats['style_performance'][style] = {
                'total': 0,
                'ratings': [],
                'avg_rating': 0
            }

        style_stats = self.generation_stats['style_performance'][style]
        style_stats['total'] += 1
        style_stats['ratings'].append(rating)
        style_stats['avg_rating'] = sum(style_stats['ratings']) / len(style_stats['ratings'])

        # Записываем результат в оптимизатор промптов для A/B тестирования
        if AB_TESTING_AVAILABLE and prompt_optimizer:
            template_id = metadata.get("_template_id")
            if template_id and template_id not in ["fallback", "error_fallback", "unknown"]:
                prompt_optimizer.record_result(
                    template_id=template_id,
                    rating=rating,
                    request=metadata.get("original_request", "")
                )
                logger.info(f"A/B test result recorded: template={template_id}, rating={rating}")

        logger.info(
            f"Feedback recorded: rating={rating}, style={style}, template={metadata.get('_template_name', 'N/A')}")

    def get_statistics(self) -> Dict:
        """Возвращает статистику генерации"""
        success_rate = (
            self.generation_stats['successful'] / self.generation_stats['total'] * 100
            if self.generation_stats['total'] > 0 else 0
        )

        # Средний рейтинг по всем отзывам
        all_ratings = [f['rating'] for f in self.generation_stats['feedback']]
        avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else 0

        # Статистика по шаблонам
        template_stats = {}
        if self.generation_stats['feedback']:
            for feedback in self.generation_stats['feedback']:
                template = feedback.get('template_name', 'Unknown')
                if template not in template_stats:
                    template_stats[template] = {'count': 0, 'ratings': []}
                template_stats[template]['count'] += 1
                template_stats[template]['ratings'].append(feedback['rating'])

            # Вычисляем средние оценки
            for template in template_stats:
                ratings = template_stats[template]['ratings']
                template_stats[template]['avg_rating'] = sum(ratings) / len(ratings)

        return {
            "total_generations": self.generation_stats['total'],
            "successful": self.generation_stats['successful'],
            "failed": self.generation_stats['failed'],
            "success_rate": f"{success_rate:.1f}%",
            "average_rating": f"{avg_rating:.2f}",
            "total_feedback": len(self.generation_stats['feedback']),
            "style_performance": self.generation_stats['style_performance'],
            "template_performance": template_stats,
            "cache_size": {
                "translations": len(self.translation_cache),
                "prompts": len(self.prompt_cache)
            },
            "ab_testing_enabled": AB_TESTING_AVAILABLE
        }

    async def check_queue_position(self, user_id: int) -> int:
        """Проверяет позицию в очереди генерации"""
        return 0

    def estimate_generation_time(self, style: str) -> int:
        """Оценивает время генерации в секундах"""
        if self.is_mvp:
            return 1
        else:
            # Разное время для разных стилей
            style_times = {
                'minimalist': 12,
                'cartoon': 15,
                'anime': 18,
                'pixel': 15,
                'watercolor': 18,
                'realistic': 20,
                '3d': 20
            }
            return style_times.get(style, 15)

    @log_function
    async def generate_sticker_image(self, prompt: str, style: str = 'default',
                                     enhance_prompt: bool = True) -> Optional[Image.Image]:
        """
        Обратная совместимость - генерирует изображение стикера

        Args:
            prompt: Текстовое описание
            style: Стиль генерации
            enhance_prompt: Улучшить промпт для лучшей генерации

        Returns:
            PIL Image объект или None при ошибке
        """
        # Используем новый метод с user_id=None для обратной совместимости
        image_bytes, metadata = await self.generate_sticker_with_validation(prompt, style, user_id=None)

        if image_bytes:
            return Image.open(io.BytesIO(image_bytes))
        return None


# Создаем единственный экземпляр сервиса
image_service = ImageGenerationService()

# Экспортируем для удобства
__all__ = ["image_service", "ImageGenerationService"]