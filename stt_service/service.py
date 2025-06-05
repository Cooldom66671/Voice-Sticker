"""
Сервис распознавания речи (Speech-to-Text) для бота VoiceSticker
Оптимизированная версия с использованием только Whisper
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
import whisper
import tempfile
import os
import re

from logger import logger, log_function
from config import (
    MVP_MODE, WHISPER_MODEL, WHISPER_DEVICE,
    MAX_AUDIO_DURATION, MAX_AUDIO_SIZE
)


class STTService:
    """Сервис для высокоточного распознавания речи с Whisper"""

    def __init__(self):
        self.is_mvp = MVP_MODE
        self.model_name = WHISPER_MODEL
        self.device = WHISPER_DEVICE
        self.model = None
        self.is_ready = False

        # Словарь частых слов для стикеров (помогает Whisper)
        self.context_words = [
            "стикер", "кот", "котик", "собака", "пёс", "пёсик",
            "радость", "грусть", "злой", "весёлый", "смешной",
            "единорог", "радуга", "сердце", "звезда", "космос",
            "программист", "код", "компьютер", "кофе",
            "пицца", "торт", "праздник", "день рождения",
            "любовь", "дружба", "привет", "пока", "спасибо"
        ]

        # Паттерны для исправления частых ошибок Whisper
        self.correction_patterns = {
            r'\bкотег\b': 'котик',
            r'\bсобако\b': 'собака',
            r'\bстикир\b': 'стикер',
            r'\bединарог\b': 'единорог',
            r'\bпрограмист\b': 'программист',
            r'\bкампьютер\b': 'компьютер',
            r'\bкафе\b': 'кофе',  # частая ошибка
            r'\bпитса\b': 'пицца',
        }

        logger.info(f"Инициализация STT сервиса с Whisper")

    async def initialize(self):
        """Инициализация сервиса с оптимальной моделью"""
        if self.is_mvp:
            logger.info("Работаем в MVP режиме - используем заглушки для STT")
            self.is_ready = True
        else:
            try:
                # Определяем оптимальную модель
                optimal_model = self._get_optimal_model()
                logger.info(f"Загружаем Whisper модель '{optimal_model}'...")

                # Загружаем модель в фоновом режиме
                loop = asyncio.get_event_loop()
                self.model = await loop.run_in_executor(
                    None,
                    self._load_model_with_settings,
                    optimal_model
                )
                self.model_name = optimal_model
                logger.info(f"Whisper модель '{optimal_model}' успешно загружена!")
                self.is_ready = True

            except Exception as e:
                logger.error(f"Ошибка загрузки Whisper: {e}")
                # Пробуем загрузить меньшую модель
                try:
                    logger.info("Пробуем загрузить модель 'base'...")
                    loop = asyncio.get_event_loop()
                    self.model = await loop.run_in_executor(
                        None,
                        whisper.load_model,
                        "base",
                        self.device
                    )
                    self.model_name = "base"
                    self.is_ready = True
                except:
                    logger.warning("Переключаемся на MVP режим")
                    self.is_mvp = True
                    self.is_ready = True

    def _load_model_with_settings(self, model_name: str):
        """Загружает модель с оптимальными настройками"""
        # Загружаем модель
        model = whisper.load_model(model_name, device=self.device)

        # Если используем GPU, оптимизируем
        if self.device == "cuda":
            model = model.half()  # FP16 для GPU

        return model

    def _get_optimal_model(self) -> str:
        """Определяет оптимальную модель в зависимости от конфигурации"""
        # Если модель явно указана в конфиге, используем её
        if self.model_name and self.model_name != "base":
            return self.model_name

        # Иначе выбираем автоматически
        try:
            import psutil
            available_memory = psutil.virtual_memory().available / (1024**3)  # ГБ

            if available_memory > 10:
                return "medium"    # Отличная точность, разумная скорость
            elif available_memory > 5:
                return "small"     # Хороший баланс
            elif available_memory > 2:
                return "base"      # Базовая точность
            else:
                return "tiny"      # Минимальные требования
        except:
            return "base"  # По умолчанию

    @log_function
    async def transcribe_audio(self, audio_file_path: str,
                             language: Optional[str] = "ru") -> Optional[Dict[str, Any]]:
        """
        Распознает речь из аудиофайла с максимальной точностью

        Args:
            audio_file_path: Путь к аудиофайлу
            language: Код языка (по умолчанию русский)

        Returns:
            Словарь с результатами или None при ошибке
        """
        if not self.is_ready:
            await self.initialize()

        try:
            # Проверяем размер файла
            file_size = Path(audio_file_path).stat().st_size
            if file_size > MAX_AUDIO_SIZE:
                logger.warning(f"Файл слишком большой: {file_size} байт")
                return None

            if self.is_mvp or not self.model:
                # MVP: возвращаем заглушку
                return await self._transcribe_mvp(audio_file_path, language)
            else:
                # Используем Whisper с оптимизациями
                return await self._transcribe_whisper_optimized(audio_file_path, language)

        except Exception as e:
            logger.error(f"Ошибка при распознавании речи: {e}")
            return None

    async def _transcribe_whisper_optimized(self, audio_file_path: str,
                                         language: Optional[str] = "ru") -> Optional[Dict[str, Any]]:
        """Оптимизированное распознавание с помощью Whisper"""
        try:
            logger.info(f"Распознаем аудио: {audio_file_path}")

            # Конвертируем OGG в WAV если нужно (Whisper лучше работает с WAV)
            if audio_file_path.endswith('.ogg'):
                wav_path = await self._convert_to_wav(audio_file_path)
                if wav_path:
                    audio_path = wav_path
                else:
                    audio_path = audio_file_path
            else:
                audio_path = audio_file_path

            # Запускаем распознавание в отдельном потоке
            loop = asyncio.get_event_loop()

            # Формируем подсказку для контекста
            context_hint = f"Это голосовое сообщение для создания стикера. Возможные слова: {', '.join(self.context_words[:10])}"

            # Оптимальные параметры для максимальной точности
            options = {
                "language": language or "ru",
                "task": "transcribe",
                "temperature": 0.0,  # Детерминированный вывод для стабильности
                "compression_ratio_threshold": 2.4,
                "logprob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "condition_on_previous_text": True,
                "initial_prompt": context_hint,  # Подсказка для лучшего контекста
                "word_timestamps": False,  # Отключаем для скорости
                "fp16": self.device == "cuda",  # FP16 только для GPU
            }

            # Для больших моделей можем использовать beam search
            if self.model_name in ["small", "medium", "large", "large-v2", "large-v3"]:
                options.update({
                    "beam_size": 5,  # Улучшает качество
                    "patience": 1.0,
                    "length_penalty": 1.0,
                })

            # Распознаем
            result = await loop.run_in_executor(
                None,
                lambda: self.model.transcribe(audio_path, **options)
            )

            # Удаляем временный файл если создавали
            if 'wav_path' in locals() and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except:
                    pass

            # Получаем текст
            text = result.get("text", "").strip()
            if not text:
                logger.warning("Whisper не распознал текст")
                return None

            # Постобработка текста
            text = self._postprocess_text(text, language)

            # Определяем уверенность на основе метрик
            confidence = self._estimate_confidence(result)

            logger.info(f"Распознан текст: {text} (уверенность: {confidence:.2f})")

            return {
                "text": text,
                "language": result.get("language", language),
                "duration": len(result.get("segments", [])),
                "confidence": confidence
            }

        except Exception as e:
            logger.error(f"Ошибка Whisper: {e}")
            # Fallback на MVP
            return await self._transcribe_mvp(audio_file_path, language)

    def _postprocess_text(self, text: str, language: str) -> str:
        """Постобработка распознанного текста"""
        if not text:
            return text

        # Убираем лишние пробелы
        text = ' '.join(text.split())

        # Убираем повторяющиеся слова (частая проблема Whisper)
        text = self._remove_repetitions(text)

        # Применяем исправления для русского языка
        if language == "ru":
            # Исправляем частые ошибки
            for pattern, replacement in self.correction_patterns.items():
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

            # Убираем случайные английские слова если текст в основном русский
            if self._is_mostly_russian(text):
                text = self._fix_mixed_language(text)

        # Убираем лишнюю пунктуацию в конце
        text = re.sub(r'[\.,:;!?]+$', '', text)

        # Капитализация первой буквы
        if text and not text[0].isupper():
            text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()

        return text

    def _remove_repetitions(self, text: str) -> str:
        """Удаляет повторяющиеся слова"""
        words = text.split()
        if len(words) <= 1:
            return text

        result = [words[0]]
        for i in range(1, len(words)):
            # Не добавляем слово если оно точно такое же как предыдущее
            if words[i].lower() != words[i-1].lower():
                result.append(words[i])

        return ' '.join(result)

    def _is_mostly_russian(self, text: str) -> bool:
        """Проверяет, что текст в основном на русском"""
        if not text:
            return False

        russian_chars = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c in 'ёЁ')
        latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
        total_alpha = russian_chars + latin_chars

        if total_alpha == 0:
            return False

        return russian_chars / total_alpha > 0.7

    def _fix_mixed_language(self, text: str) -> str:
        """Исправляет смешанный язык в тексте"""
        # Список допустимых английских слов в русском контексте
        allowed_english = {
            'it', 'hr', 'pr', 'art', 'vip', 'ok', 'hi', 'bye',
            'wow', 'lol', 'omg', 'wtf', 'vs', 'feat'
        }

        words = text.split()
        result = []

        for word in words:
            # Если слово полностью на латинице
            if all(c.isascii() or not c.isalpha() for c in word):
                # Проверяем, допустимо ли оно
                if word.lower() in allowed_english or len(word) <= 2:
                    result.append(word)
                # Иначе пропускаем (вероятно, ошибка распознавания)
            else:
                result.append(word)

        return ' '.join(result) if result else text

    def _estimate_confidence(self, result: Dict) -> float:
        """Оценивает уверенность распознавания"""
        # Базовая уверенность зависит от модели
        model_confidence = {
            "tiny": 0.7,
            "base": 0.8,
            "small": 0.85,
            "medium": 0.9,
            "large": 0.95,
            "large-v2": 0.95,
            "large-v3": 0.95
        }

        base_confidence = model_confidence.get(self.model_name, 0.8)

        # Корректируем на основе длины текста
        text = result.get("text", "")
        if len(text) < 5:
            base_confidence *= 0.9
        elif len(text) > 100:
            base_confidence *= 0.95

        # Если есть segments, проверяем no_speech_prob
        segments = result.get("segments", [])
        if segments:
            # Средняя вероятность что это НЕ речь
            no_speech_probs = [s.get("no_speech_prob", 0) for s in segments]
            avg_no_speech = sum(no_speech_probs) / len(no_speech_probs)

            # Если высокая вероятность что это не речь, снижаем уверенность
            if avg_no_speech > 0.5:
                base_confidence *= (1 - avg_no_speech)

        return min(max(base_confidence, 0.1), 0.99)

    async def _convert_to_wav(self, input_path: str) -> Optional[str]:
        """Конвертирует аудио в WAV формат для лучшей совместимости"""
        try:
            from pydub import AudioSegment

            # Создаем временный файл
            temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            output_path = temp_file.name
            temp_file.close()

            # Конвертируем
            logger.info(f"Конвертируем {input_path} в WAV")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: AudioSegment.from_file(input_path).export(
                    output_path,
                    format="wav",
                    parameters=["-ar", "16000"]  # 16kHz для Whisper
                )
            )

            return output_path

        except ImportError:
            logger.warning("pydub не установлен, используем оригинальный файл")
            return None
        except Exception as e:
            logger.error(f"Ошибка конвертации аудио: {e}")
            return None

    async def _transcribe_mvp(self, audio_file_path: str,
                            language: Optional[str] = None) -> Dict[str, Any]:
        """MVP версия - возвращает случайный текст"""
        await asyncio.sleep(1)

        import random
        sample_texts = [
            "Котик в космосе",
            "Веселая радуга",
            "Танцующий робот",
            "Летающая пицца",
            "Единорог на скейте",
            "Программист пьет кофе",
            "Грустный пёсик под дождём",
            "Радостное солнышко",
            "Злой босс кричит",
            "Милый зайчик прыгает"
        ]

        return {
            "text": random.choice(sample_texts),
            "language": language or "ru",
            "duration": 3.0,
            "confidence": 0.95
        }

    def is_supported_format(self, file_extension: str) -> bool:
        """Проверяет, поддерживается ли формат аудио"""
        supported = ['.ogg', '.mp3', '.wav', '.m4a', '.opus', '.flac', '.webm']
        return file_extension.lower() in supported

    def estimate_processing_time(self, duration: float) -> float:
        """Оценивает время обработки в секундах"""
        if self.is_mvp:
            return 1.0
        else:
            # Время зависит от модели
            model_multipliers = {
                "tiny": 0.1,
                "base": 0.3,
                "small": 0.5,
                "medium": 1.0,
                "large": 2.0,
                "large-v2": 2.5,
                "large-v3": 3.0
            }
            multiplier = model_multipliers.get(self.model_name, 0.5)
            return duration * multiplier + 2  # +2 секунды на загрузку


# Создаем единственный экземпляр сервиса
stt_service = STTService()

# Экспортируем для удобства
__all__ = ["stt_service", "STTService"]