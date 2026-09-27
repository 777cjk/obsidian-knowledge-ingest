import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import parser_adapter


def _minimal_pdf(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects.append(
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"\nendstream"
    )

    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(data)
    data += f"xref\n0 {len(offsets)}\n".encode("ascii")
    data += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode("ascii")
    data += (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(data)


class ParserAdapterTests(unittest.TestCase):
    def test_text_fixture_parse_has_common_schema_and_source_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.md"
            raw = "# Title\n\nbody\n".encode("utf-8")
            path.write_bytes(raw)

            result = parser_adapter.parse(path)

            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["text"], "# Title\n\nbody\n")
            self.assertEqual(result["source"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result["source"]["size_bytes"], len(raw))
            self.assertEqual(result["parser"], {"name": "text-fixture", "version": "1"})
            self.assertEqual(result["metadata"]["line_count"], 3)
            self.assertEqual(result["outline"], [{"kind": "heading", "level": 1, "title": "Title", "line": 1}])
            self.assertEqual(result["assets"], [])
            self.assertEqual(result["errors"], [])

    def test_invalid_utf8_returns_error_with_same_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.txt"
            path.write_bytes(b"\xff\xfe\xfd")

            result = parser_adapter.parse(path)

            self.assertEqual(result["status"], "error")
            self.assertIsNone(result["text"])
            self.assertEqual(result["errors"][0]["code"], "decode_error")
            self.assertIn("sha256", result["source"])

    def test_optional_backends_are_declared_lazy_and_ocr_is_preprocessor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            path.write_bytes(b"%PDF-fixture")

            catalog = parser_adapter.backend_catalog()
            result = parser_adapter.parse(path, backend="docling")
            ocr_result = parser_adapter.parse(path, backend="ocrmypdf")

            self.assertTrue(catalog["docling"]["optional"])
            self.assertFalse(catalog["docling"]["registered"])
            self.assertIn(result["status"], {"unavailable", "error"})
            if result["status"] == "unavailable":
                self.assertIn(result["errors"][0]["code"], {"backend_dependency_missing", "backend_import_error"})
            self.assertEqual(ocr_result["status"], "unsupported")
            self.assertEqual(ocr_result["errors"][0]["code"], "preprocessor_only")

    def test_auto_routes_office_documents_to_optional_markitdown(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "example.docx"
            path.write_bytes(b"fixture")

            result = parser_adapter.parse(path)

            self.assertEqual(result["parser"]["name"], "markitdown")
            self.assertIn(result["status"], {"unavailable", "error", "ok"})

    @unittest.skipUnless(importlib.util.find_spec("markitdown"), "MarkItDown is not installed in this Python environment")
    def test_markitdown_parses_html_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.html"
            path.write_text("<html><body><h1>Canary title</h1><p>Source text.</p></body></html>", encoding="utf-8")

            result = parser_adapter.parse(path, backend="markitdown")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parser"]["name"], "markitdown")
            self.assertIn("Canary title", result["text"])
            self.assertIn("Source text", result["text"])

    @unittest.skipUnless(importlib.util.find_spec("liteparse"), "LiteParse is not installed in this Python environment")
    def test_liteparse_parses_pdf_fixture_with_page_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.pdf"
            path.write_bytes(_minimal_pdf("LiteParse canary"))

            result = parser_adapter.parse(path, backend="liteparse")

            self.assertEqual(result["status"], "ok", result["errors"])
            self.assertEqual(result["parser"]["name"], "liteparse")
            self.assertIn("LiteParse canary", result["text"])
            self.assertEqual(result["metadata"]["page_count"], 1)
            self.assertEqual(result["metadata"]["source_page_refs"], [{"page": 1}])

    def test_cli_emits_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.md"
            path.write_text("# CLI canary", encoding="utf-8")
            script = Path(__file__).parents[1] / "scripts" / "parser_adapter.py"

            completed = subprocess.run(
                [sys.executable, str(script), "parse", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(json.loads(completed.stdout)["status"], "ok")

    def test_missing_file_returns_schema_error(self):
        result = parser_adapter.parse("/path/that/does/not/exist.txt")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["errors"][0]["code"], "source_not_found")


if __name__ == "__main__":
    unittest.main()
