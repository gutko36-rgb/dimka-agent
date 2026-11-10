import base64
import unittest
from types import SimpleNamespace

from media_input import extract_image_text, transcribe_audio


class FakeTranscriptions:
    def __init__(self, result="Распознанный текст"):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.result)


class FakeChatCompletions:
    def __init__(self, content="Извлечённый текст"):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeAI:
    def __init__(self):
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


class MediaInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_audio_uses_russian_and_multilingual_model(self):
        ai = FakeAI()
        result = await transcribe_audio(
            ai, b"audio-bytes", filename="voice.ogg",
            model="whisper-large-v3-turbo",
        )
        self.assertEqual(result, "Распознанный текст")
        call = ai.audio.transcriptions.calls[0]
        self.assertEqual(call["model"], "whisper-large-v3-turbo")
        self.assertEqual(call["language"], "ru")
        self.assertEqual(call["response_format"], "text")
        self.assertEqual(call["file"], ("voice.ogg", b"audio-bytes"))

    async def test_transcribe_audio_rejects_empty_or_unknown_format(self):
        ai = FakeAI()
        with self.assertRaises(ValueError):
            await transcribe_audio(ai, b"", filename="voice.ogg")
        with self.assertRaises(ValueError):
            await transcribe_audio(ai, b"audio", filename="voice.exe")
        self.assertEqual(ai.audio.transcriptions.calls, [])

    async def test_image_ocr_sends_data_url_and_extraction_prompt(self):
        ai = FakeAI()
        image = b"image-bytes"
        result = await extract_image_text(
            ai, image, mime_type="image/png", model="qwen/qwen3.8-27b"
        )
        self.assertEqual(result, "Извлечённый текст")
        call = ai.chat.completions.calls[0]
        self.assertEqual(call["model"], "qwen/qwen3.8-27b")
        content = call["messages"][0]["content"]
        self.assertIn("Извлеки видимый текст", content[0]["text"])
        self.assertEqual(
            content[1]["image_url"]["url"],
            "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        )

    async def test_image_ocr_rejects_unsupported_mime(self):
        ai = FakeAI()
        with self.assertRaises(ValueError):
            await extract_image_text(ai, b"image", mime_type="application/pdf")
        self.assertEqual(ai.chat.completions.calls, [])


if __name__ == "__main__":
    unittest.main()
