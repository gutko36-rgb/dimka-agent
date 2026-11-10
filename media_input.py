"""Optional Groq-backed voice transcription and image OCR.

Both features are opt-in at the Telegram handler layer. This module never writes
to Supabase and never executes extracted text as instructions.
"""
from __future__ import annotations

import base64
from typing import Any


MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024

AUDIO_EXTENSIONS = {".ogg", ".mp3", ".m4a", ".wav", ".webm", ".mp4", ".mpeg", ".mpga", ".flac"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def transcribe_audio(
    ai_client: Any,
    audio_bytes: bytes,
    *,
    filename: str,
    model: str = "whisper-large-v3-turbo",
    language: str = "ru",
) -> str:
    if not audio_bytes:
        raise ValueError("Аудиофайл пустой")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("Аудиофайл больше 20 МБ. Отправь более короткое голосовое сообщение.")
    if not filename or "." not in filename or filename.rsplit(".", 1)[-1].lower() not in {
        ext[1:] for ext in AUDIO_EXTENSIONS
    }:
        raise ValueError("Формат аудио не поддерживается")
    if not model:
        raise ValueError("Не задана модель распознавания речи")

    result = await ai_client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=model,
        language=language,
        prompt=(
            "Распознавай русскую речь. Сохраняй имена, названия, суммы, даты и числа "
            "так, как они произнесены. Не додумывай неразборчивые слова."
        ),
        response_format="text",
        temperature=0.0,
    )
    text = getattr(result, "text", result)
    return str(text or "").strip()


async def extract_image_text(
    ai_client: Any,
    image_bytes: bytes,
    *,
    mime_type: str,
    model: str = "qwen/qwen3.8-27b",
) -> str:
    if not image_bytes:
        raise ValueError("Изображение пустое")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Изображение больше 15 МБ. Отправь файл меньшего размера.")
    normalized_mime = (mime_type or "").lower().strip()
    if normalized_mime not in IMAGE_MIME_TYPES:
        raise ValueError("Для OCR поддерживаются JPEG, PNG и WEBP")
    if not model:
        raise ValueError("Не задана модель распознавания изображений")

    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = await ai_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Извлеки видимый текст с изображения. Сохраняй формулировки, "
                            "цифры, даты и порядок строк. Не пересказывай и не додумывай. "
                            "Неразборчивые места помечай как [неразборчиво]. "
                            "Если текста нет, ответь: Текст не обнаружен."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{normalized_mime};base64,{encoded}",
                        },
                    },
                ],
            }
        ],
        temperature=0.0,
        max_completion_tokens=1800,
    )
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return str(content or "").strip()
