import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import baidu_netdisk_ingest as ingest_module


def record(*, content=None, abstract=None, fsid="123", filename="project.md"):
    return {
        "fsid": fsid,
        "path": f"/AI/{filename}",
        "filename": filename,
        "md5": "remote-md5",
        "category": 4,
        "size": 42,
        "content": content,
        "abstract": abstract,
    }


class FakeTool:
    def __init__(self, name, properties=None, required=None):
        self.name = name
        self.inputSchema = {
            "type": "object",
            "properties": properties or {"dir": {"type": "string"}, "page": {"type": "integer"}},
            "required": required or ["dir"],
        }


class FakeMcpSession:
    def __init__(self, records, tools=None):
        self.records = records
        self.tools = tools or [FakeTool("file_list")]
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"structuredContent": {"list": self.records}}


class FakeConnect:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class BaiduNetdiskIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stage = self.root / "staging"

    def tearDown(self):
        self.temp.cleanup()

    def test_segments_create_candidate_but_mark_completeness_unverified(self):
        result = ingest_module.ingest(
            [record(content=[{"text": "first segment"}, {"text": "second segment"}])],
            self.stage,
            "/AI",
        )
        self.assertEqual(result["counts"]["imported"], 1)
        self.assertEqual(result["counts"]["candidates"], 1)
        manifest = json.loads((self.stage / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entries"][0]
        self.assertEqual(entry["retrieval_status"], "platform_segments")
        self.assertEqual(entry["content_completeness"], "unverified_platform_segments")
        self.assertFalse(entry["content_completeness_verified"])
        candidate = next((self.stage / "candidates").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Full-file completeness was not verified", candidate)
        self.assertIn("first segment", candidate)

    def test_metadata_only_does_not_create_candidate(self):
        result = ingest_module.ingest([record()], self.stage, "/AI")
        self.assertEqual(result["counts"]["metadata_only"], 1)
        self.assertEqual(list((self.stage / "candidates").glob("*.md")), [])
        manifest = json.loads((self.stage / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["entries"][0]["retrieval_status"], "metadata_only")

    def test_abstract_only_is_distinguished_from_segments(self):
        ingest_module.ingest([record(abstract="short platform abstract")], self.stage, "/AI")
        manifest = json.loads((self.stage / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entries"][0]
        self.assertEqual(entry["retrieval_status"], "abstract_only")
        self.assertEqual(entry["content_completeness"], "abstract_only")

    def test_same_snapshot_is_idempotent_and_updated_snapshot_is_versioned(self):
        first = record(content="v1")
        one = ingest_module.ingest([first], self.stage, "/AI")
        two = ingest_module.ingest([first], self.stage, "/AI")
        self.assertEqual(one["counts"]["imported"], 1)
        self.assertEqual(two["counts"]["unchanged"], 1)
        changed = dict(first, content="v2")
        three = ingest_module.ingest([changed], self.stage, "/AI")
        self.assertEqual(three["counts"]["updated"], 1)
        manifest = json.loads((self.stage / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["entries"][0]["version"], 2)
        self.assertEqual(len(manifest["history"]), 1)

    def test_bad_remote_path_fails_before_writing(self):
        with self.assertRaisesRegex(ingest_module.IngestError, "absolute netdisk directory"):
            ingest_module.ingest([], self.stage, "relative")
        self.assertFalse((self.stage / "manifest.json").exists())

    def test_live_fetch_allows_only_read_tool_and_encodes_slash(self):
        session = FakeMcpSession([record(content="live")])
        with mock.patch.dict(os.environ, {"BAIDU_NETDISK_ACCESS_TOKEN": "fixture-token"}):
            with mock.patch.object(ingest_module, "_connect", return_value=FakeConnect(session)):
                records, limited = asyncio.run(
                    ingest_module.fetch_records("/AI/资料", "file_list", 2, 10)
                )
        self.assertTrue(limited)
        self.assertEqual(len(records), 1)
        self.assertEqual(session.calls[0][0], "file_list")
        self.assertEqual(session.calls[0][1]["dir"], "%2FAI%2F%E8%B5%84%E6%96%99")
        with self.assertRaisesRegex(ingest_module.IngestError, "read-only allowlist"):
            asyncio.run(ingest_module.fetch_records("/AI", "file_meta", 1, 1))

    def test_live_fetch_missing_token_is_sanitized(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ingest_module.IngestError, "ACCESS_TOKEN is not configured"):
                asyncio.run(ingest_module.fetch_records("/AI", "file_list", 1, 1))

    def test_staging_outputs_are_private(self):
        ingest_module.ingest([record(content="private")], self.stage, "/AI")
        for path in (self.stage, self.stage / ".source", self.stage / "candidates"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for path in [self.stage / "manifest.json", *self.stage.glob("candidates/*.md"), *self.stage.glob(".source/baidu-netdisk/*.json")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
