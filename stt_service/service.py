"""
Сервис распознавания речи (Speech-to-Text) для бота VoiceSticker
Использует OpenAI Whisper
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
import whisper
import tempfile
import os

from logger import logger, log_function
from config import (
    MVP_MODE, WHISPER_MODEL, WHISPER_DEVICE,
    MAX_AUDIO_DURATION, MAX_AUDIO_SIZE
)


class STTService:
    """Сервис для распознавания речи"""

    def __init__(self):
        self.is_mvp = MVP_MODE
        self.model_name = WHISPER_MODEL
        self.device = WHISPER_DEVICE
        self.model = None
        self.is_ready = False

        logger.info(f"Инициализация STT сервиса (MVP mode: {self.is_mvp})")

    async def initialize(self):
        """Инициализация сервиса"""
        if self.is_mvp:
            logger.info("Работаем в MVP режиме - используем заглушки для STT")
            self.is_ready = True
        else:
            try:
                logger.info(f"Загружаем Whisper модель '{self.model_name}'...")
                # Загружаем модель в фоновом режиме
                loop = asyncio.get_event_loop()
                self.model = await loop.run_in_executor(
                    None,
                    whisper.load_model,
                    self.model_name,
                    self.device
                )
                logger.info("Whisper модель успешно загружена!")
                self.is_ready = True
            except Exception as e:
                logger.error(f"Ошибка загрузки Whisper: {e}")
                logger.warning("Переключаемся на MVP режим")
                self.is_mvp = True
                self.is_ready = True

    @log_function
    async def transcribe_audio(self, audio_file_path: str,
                             language: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Распознает речь из аудиофайла

        Args:
            audio_file_path: Путь к аудиофайлу
            language: Код языка (опционально)

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
                # Используем Whisper
                return await self._transcribe_whisper(audio_file_path, language)

        except Exception as e:
            logger.error(f"Ошибка при распознавании речи: {e}")
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
            "Единорог на скейте"
        ]

        return {
            "text": random.choice(sample_texts),
            "language": language or "ru",
            "duration": 3.0,
            "confidence": 0.95
        }

    async def _transcribe_whisper(self, audio_file_path: str,
                                language: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Распознавание с помощью Whisper"""
        try:
            logger.info(f"Распознаем аудио: {audio_file_path}")

            # Конвертируем OGG в WAV если нужно
            if audio_file_path.endswith('.ogg'):
                wav_path = await self._convert_to_wav(audio_file_path)
                if not wav_path:
                    return None
                audio_file_path = wav_path

            # Запускаем распознавание в отдельном потоке
            loop = asyncio.get_event_loop()

            # Параметры для whisper
            options = {
                "language": language,
                "task": "transcribe",
                "fp16": False  # Отключаем для стабильности на CPU
            }

            # Распознаем
            result = await loop.run_in_executor(
                None,
                lambda: self.model.transcribe(audio_file_path, **options)
            )

            # Удаляем временный файл если создавали
            if 'wav_path' in locals():
                try:
                    os.unlink(wav_path)
                except:
                    pass

            # Формируем ответ
            text = result.get("text", "").strip()
            if not text:
                logger.warning("Whisper не распознал текст")
                return None

            detected_language = result.get("language", language or "ru")

            logger.info(f"Распознан текст: {text}")

            return {
                "text": text,
                "language": detected_language,
                "duration": len(result.get("segments", [])),
                "confidence": 0.9  # Whisper не дает confidence score
            }

        except Exception as e:
            logger.error(f"Ошибка Whisper: {e}")
            # Fallback на MVP
            return await self._transcribe_mvp(audio_file_path, language)

    async def _convert_to_wav(self, input_path: str) -> Optional[str]:
        """Конвертирует аудио в WAV формат"""
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
                lambda: AudioSegment.from_ogg(input_path).export(
                    output_path,
                    format="wav"
                )
            )

            return output_path

        except Exception as e:
            logger.error(f"Ошибка конвертации аудио: {e}")
            return None

    def is_supported_format(self, file_extension: str) -> bool:
        """Проверяет, поддерживается ли формат аудио"""
        supported = ['.ogg', '.mp3', '.wav', '.m4a', '.opus', '.flac']
        return file_extension.lower() in supported

    def estimate_processing_time(self, duration: float) -> float:
        """Оценивает время обработки в секундах"""
        if self.is_mvp:
            return 1.0
        else:
            # Whisper обычно работает быстрее реального времени
            return duration * 0.3 + 2  # +2 секунды на загрузку


# Создаем единственный экземпляр сервиса
stt_service = STTService()

# Экспортируем для удобства
__all__ = ["stt_service", "STTService"]