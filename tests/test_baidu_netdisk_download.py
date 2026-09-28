import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import baidu_netdisk_download as download_module


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/plain"):
        self.body = body
        self.offset = 0
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.body):
            return b""
        if size < 0:
            size = len(self.body)
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class BaiduNetdiskDownloadTests(unittest.TestCase):
    def test_download_streams_and_hashes_without_exposing_token(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "file.txt"
            response = FakeResponse(b"hello")
            with mock.patch.object(download_module, "urlopen", return_value=response) as opened:
                result = download_module.download_file("/apps/demo/file.txt", destination, "fixture-token")

            self.assertEqual(result["size_bytes"], 5)
            self.assertEqual(result["sha256"], hashlib.sha256(b"hello").hexdigest())
            self.assertEqual(destination.read_bytes(), b"hello")
            request = opened.call_args.args[0]
            self.assertTrue(request.full_url.startswith("https://d.pcs.baidu.com/"))
            self.assertEqual(request.headers["User-agent"], "pan.baidu.com")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_download_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "file.bin"
            with mock.patch.object(download_module, "urlopen", return_value=FakeResponse(b"0123456789")):
                with self.assertRaisesRegex(download_module.IngestError, "max-bytes"):
                    download_module.download_file("/apps/demo/file.bin", destination, "token", max_bytes=4)
            self.assertFalse(destination.exists())

    def test_run_writes_parsed_candidate_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "staging"
            record = {"fsid": "123", "path": "/apps/demo/file.md", "filename": "file.md", "md5": ""}
            parsed = {
                "status": "ok",
                "parser": {"name": "text-fixture", "version": "1"},
                "text": "# Candidate\n\nbody",
            }
            download = {
                "status": "downloaded",
                "remote_path": record["path"],
                "local_path": str(stage / "raw" / "file.md"),
                "size_bytes": 10,
                "sha256": "a" * 64,
                "content_type": "text/markdown",
            }
            with mock.patch.object(download_module, "download_file", return_value=download):
                with mock.patch.object(download_module, "parse", return_value=parsed):
                    result = download_module.run([record], stage, "token", "auto", 100)

            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(result["parsed"], 1)
            self.assertEqual(result["candidates"], 1)
            manifest = json.loads((stage / "download-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"][0]["source_id"], "baidu_netdisk:123")
            candidate = next((stage / "candidates").glob("*.md"))
            self.assertIn("# Candidate", candidate.read_text(encoding="utf-8"))

    def test_run_skips_office_lock_files_without_downloading(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "staging"
            record = {
                "fsid": "456",
                "path": "/docs/~$draft.docx",
                "filename": "~$draft.docx",
            }
            with mock.patch.object(download_module, "download_file") as download:
                result = download_module.run([record], stage, "token", "auto", 100)

            download.assert_not_called()
            self.assertEqual(result["downloaded"], 0)
            self.assertEqual(result["skipped"], 1)
            self.assertEqual(result["candidates"], 0)
            manifest = json.loads((stage / "download-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"][0]["status"], "skipped")
            self.assertEqual(manifest["entries"][0]["skip_reason"], "temporary_office_lock_file")


if __name__ == "__main__":
    unittest.main()
