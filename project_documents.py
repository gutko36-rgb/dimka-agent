"""Safe, bounded text extraction and search for project attachments.

Files are processed in memory only. This module never writes to disk or a database.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_PDF_PAGES = 80
MAX_TEXT_CHARS = 40_000

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


def _bounded_text(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    value = (value or "").replace("\x00", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()[:limit]


def extract_document_text(
    file_name: str | None,
    mime_type: str | None,
    content: bytes,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_TEXT_CHARS,
) -> dict[str, Any]:
    """Extract text from PDF, DOCX or plain text with strict resource bounds."""
    if not isinstance(content, bytes) or not content:
        raise ValueError("Файл пуст или имеет неподдерживаемый формат данных")
    if len(content) > max_file_bytes:
        raise ValueError("Файл превышает допустимый размер 10 МБ")

    suffix = PurePosixPath((file_name or "").replace("\\", "/")).suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("Поддерживаются PDF, DOCX, TXT, MD, CSV и LOG")

    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("Содержимое не похоже на PDF")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise ValueError("PDF защищён паролем; извлечение не выполнено")
            if len(reader.pages) > max_pages:
                raise ValueError(f"В PDF больше {max_pages} страниц")
            chunks = []
            for page in reader.pages:
                if sum(len(x) for x in chunks) >= max_chars:
                    break
                chunks.append(page.extract_text() or "")
            text = _bounded_text("\n\n".join(chunks), max_chars)
            return {
                "status": "ok" if text else "no_text",
                "text": text,
                "pages": len(reader.pages),
                "truncated": len(text) >= max_chars,
            }
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Не удалось прочитать PDF") from exc

    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_DOCX_ENTRIES:
                    raise ValueError("В DOCX слишком много внутренних элементов")
                total_size = sum(info.file_size for info in infos)
                if total_size > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("DOCX превышает лимит распакованного размера")
                if "[Content_Types].xml" not in archive.namelist() or "word/document.xml" not in archive.namelist():
                    raise ValueError("Файл не является корректным DOCX")
            from docx import Document
            document = Document(io.BytesIO(content))
            chunks = [p.text for p in document.paragraphs if p.text]
            for table in document.tables:
                for row in table.rows:
                    chunks.append(" | ".join(cell.text for cell in row.cells))
            text = _bounded_text("\n".join(chunks), max_chars)
            return {
                "status": "ok" if text else "no_text",
                "text": text,
                "pages": None,
                "truncated": len(text) >= max_chars,
            }
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Не удалось прочитать DOCX") from exc

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ValueError("Текстовый файл должен быть в UTF-8 или UTF-16") from exc
    text = _bounded_text(text, max_chars)
    return {
        "status": "ok" if text else "no_text",
        "text": text,
        "pages": None,
        "truncated": len(text) >= max_chars,
    }


def search_document_text(text: str, query: str, *, limit: int = 3, radius: int = 180) -> list[str]:
    """Return short deterministic snippets when every query token occurs in text."""
    terms = [term.casefold() for term in re.findall(r"[^\W_]+", query, flags=re.UNICODE)]
    if not terms or not text:
        return []
    folded = text.casefold()
    if not all(term in folded for term in terms):
        return []
    first = min(folded.find(term) for term in terms)
    start = max(0, first - radius)
    end = min(len(text), first + radius)
    snippet = text[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return [snippet] if snippet else []


def build_project_timeline(
    project: dict[str, Any],
    items: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build a chronological timeline from existing project/item/asset timestamps."""
    events: list[dict[str, str]] = []

    def add(timestamp: Any, label: str, description: str) -> None:
        if not timestamp:
            return
        events.append({
            "timestamp": str(timestamp),
            "label": label,
            "description": description[:240],
        })

    add(project.get("created_at"), "Проект создан", str(project.get("title") or ""))
    created = str(project.get("created_at") or "")
    updated = str(project.get("updated_at") or "")
    if updated and updated != created:
        add(updated, "Карточка проекта обновлена", "Отображается последняя дата изменения; история старых значений пока не хранится.")
    labels = {"stage": "Добавлен этап", "task": "Добавлена задача", "note": "Добавлена заметка"}
    for item in items:
        add(item.get("created_at"), labels.get(str(item.get("kind")), "Добавлена запись"), str(item.get("content") or ""))
    for asset in assets:
        name = asset.get("file_name") or asset.get("media_type") or "файл"
        add(asset.get("created_at"), "Прикреплён материал", str(name))
    events.sort(key=lambda event: event["timestamp"], reverse=True)
    return events
