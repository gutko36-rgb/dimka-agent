import io
import unittest
from datetime import datetime, timezone

from project_documents import (
    build_project_timeline,
    extract_document_text,
    search_document_text,
)


class DocumentExtractionTests(unittest.TestCase):
    def test_extracts_utf8_text(self):
        result = extract_document_text("notes.txt", "text/plain", "Пульт и сроки".encode())
        self.assertEqual(result["status"], "ok")
        self.assertIn("Пульт", result["text"])

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(ValueError):
            extract_document_text("scan.png", "image/png", b"not an image")

    def test_rejects_empty_file(self):
        with self.assertRaises(ValueError):
            extract_document_text("empty.txt", "text/plain", b"")

    def test_rejects_oversized_file(self):
        with self.assertRaises(ValueError):
            extract_document_text("big.txt", "text/plain", b"x" * 20, max_file_bytes=10)

    def test_rejects_fake_pdf(self):
        with self.assertRaises(ValueError):
            extract_document_text("fake.pdf", "application/pdf", b"not a pdf")

    def test_reads_pdf_and_reports_no_text_layer(self):
        from pypdf import PdfWriter
        stream = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        result = extract_document_text("blank.pdf", "application/pdf", stream.getvalue())
        self.assertEqual(result["status"], "no_text")
        self.assertEqual(result["pages"], 1)

    def test_enforces_pdf_page_limit(self):
        from pypdf import PdfWriter
        stream = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        with self.assertRaises(ValueError):
            extract_document_text("blank.pdf", "application/pdf", stream.getvalue(), max_pages=0)

    def test_rejects_invalid_docx(self):
        with self.assertRaises(ValueError):
            extract_document_text("fake.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK not a docx")

    def test_extracts_docx_paragraph_and_table(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("Пульт: проверить сроки")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Бюджет"
        table.cell(0, 1).text = "5000"
        stream = io.BytesIO()
        doc.save(stream)
        result = extract_document_text("brief.docx", None, stream.getvalue())
        self.assertIn("проверить сроки", result["text"])
        self.assertIn("Бюджет | 5000", result["text"])

    def test_search_requires_all_terms(self):
        text = "Пульт 2.0: проверить сроки и бюджет проекта"
        self.assertTrue(search_document_text(text, "сроки бюджет"))
        self.assertFalse(search_document_text(text, "сроки договор"))

    def test_search_is_case_insensitive(self):
        self.assertTrue(search_document_text("PROJECT PULT", "project pult"))

    def test_empty_query_has_no_results(self):
        self.assertEqual(search_document_text("anything", "  "), [])


class ProjectTimelineTests(unittest.TestCase):
    def test_merges_project_items_and_assets(self):
        timeline = build_project_timeline(
            {"title": "Пульт", "created_at": "2026-11-01T10:00:00+00:00", "updated_at": "2026-11-02T10:00:00+00:00"},
            [{"kind": "task", "content": "Проверить макет", "created_at": "2026-11-03T10:00:00+00:00"}],
            [{"file_name": "brief.pdf", "created_at": "2026-11-04T10:00:00+00:00"}],
        )
        self.assertEqual(timeline[0]["label"], "Прикреплён материал")
        self.assertEqual(len(timeline), 3 + 1)

    def test_timeline_omits_missing_timestamps(self):
        timeline = build_project_timeline({"title": "X"}, [{"kind": "note", "content": "n"}], [])
        self.assertEqual(timeline, [])


if __name__ == "__main__":
    unittest.main()
