import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import manifest_scan


class ManifestScanTests(unittest.TestCase):
    def test_scan_tracks_new_modified_duplicate_and_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            stage = Path(temp) / "stage"
            root.mkdir()
            (root / "a.md").write_text("same", encoding="utf-8")
            (root / "b.md").write_text("same", encoding="utf-8")
            args = type("Args", (), {
                "root": str(root), "label": "test", "manifest": str(stage / "manifest.json"),
                "output_dir": str(stage), "emit_candidates": True, "exclude": [],
            })
            first = manifest_scan.scan(args)
            self.assertEqual(first["counts"]["new"], 2)
            self.assertEqual(first["counts"]["duplicate"], 1)
            self.assertEqual(len(list((stage / "candidates").glob("*.md"))), 2)
            (root / "a.md").write_text("changed", encoding="utf-8")
            (root / "b.md").unlink()
            second = manifest_scan.scan(args)
            self.assertEqual(second["counts"]["modified"], 1)
            self.assertEqual(second["counts"]["deleted"], 1)
            data = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(data["entries"][0]["version"], 2)
            self.assertTrue(data["entries"][0]["supersedes"])
            self.assertEqual(data["entries"][0]["revision_id"].rsplit("@v", 1)[-1], "2")
            self.assertEqual(len(data["history"]), 1)


if __name__ == "__main__":
    unittest.main()
