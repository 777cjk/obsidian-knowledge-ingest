import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import feishu_minutes_ingest as ingest_module


TOKEN = "obcnFixtureToken_01"
TRANSCRIPT_PATH = f"artifact-Team Sync-{TOKEN}/transcript.txt"
SOURCE_ID = f"feishu_minutes:{TOKEN}"


class FixtureLarkCLI:
    def __init__(self, texts=None, *, scope_ok=True, fetch_returncode=0, transcript_path=TRANSCRIPT_PATH, url="https://team.feishu.cn/minutes/obcnFixtureToken_01"):
        self.texts = list(texts or ["Alice 00:00:01\nWe agreed to ship the first slice.\n"])
        self.scope_ok = scope_ok
        self.fetch_returncode = fetch_returncode
        self.transcript_path = transcript_path
        self.url = url
        self.calls = []

    def __call__(self, command, cwd):
        cwd = Path(cwd)
        self.calls.append((list(command), cwd))
        if command[1:3] == ["auth", "check"]:
            self.assert_auth_command(command)
            code = 0 if self.scope_ok else 1
            output = json.dumps({"ok": self.scope_ok})
            return CompletedProcess(command, code, output, "fixture auth diagnostic")

        self.assert_minutes_command(command)
        if self.fetch_returncode:
            return CompletedProcess(command, self.fetch_returncode, "sensitive raw cli output", "fixture retrieval diagnostic")

        if self.transcript_path.startswith("/"):
            transcript_path = Path(self.transcript_path)
        else:
            transcript_path = cwd / self.transcript_path
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        try:
            transcript_path.resolve().relative_to(cwd.resolve())
            is_within_source = True
        except ValueError:
            is_within_source = False
        if is_within_source:
            transcript_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(transcript_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
        payload = {
            "ok": True,
            "data": {
                "minutes": [{
                    "minute_token": TOKEN,
                    "title": "Team Sync",
                    "artifacts": {"transcript_file": self.transcript_path},
                }]
            },
        }
        if self.url is not None:
            payload["data"]["minutes"][0]["url"] = self.url
        return CompletedProcess(command, 0, json.dumps(payload), "")

    @staticmethod
    def assert_auth_command(command):
        if command != ["fake-lark", "auth", "check", "--scope", "minutes:minutes.artifacts:read", "--json"]:
            raise AssertionError(f"unexpected auth command: {command}")

    @staticmethod
    def assert_minutes_command(command):
        expected = [
            "fake-lark", "minutes", "+detail", "--as", "user", "--minute-tokens", TOKEN,
            "--transcript", "--output-dir", ".", "--json",
        ]
        if command != expected:
            raise AssertionError(f"unexpected minutes command: {command}")


class FeishuMinutesIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.staging = self.root / "staging"

    def tearDown(self):
        self.temp.cleanup()

    def _ingest(self, runner, token=TOKEN):
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            return ingest_module.ingest(token, self.staging, lark_cli="fake-lark")

    def test_new_import_writes_private_manifest_candidate_and_transcript(self):
        runner = FixtureLarkCLI()
        result = self._ingest(runner)

        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["provider"], "feishu_minutes")
        self.assertEqual(result["version"], 1)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual([call[0] for call in runner.calls][0], [
            "fake-lark", "auth", "check", "--scope", "minutes:minutes.artifacts:read", "--json",
        ])
        self.assertEqual([call[0] for call in runner.calls][1], [
            "fake-lark", "minutes", "+detail", "--as", "user", "--minute-tokens", TOKEN,
            "--transcript", "--output-dir", ".", "--json",
        ])
        self.assertEqual(runner.calls[0][1], self.staging.resolve())
        self.assertTrue(runner.calls[1][1].parent == (self.staging / ".source").resolve())

        manifest_path = self.staging / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["source_id"], SOURCE_ID)
        self.assertEqual(entry["revision_id"], f"{SOURCE_ID}@v1")
        self.assertEqual(entry["provider"], "feishu_minutes")
        self.assertEqual(entry["permissions"], "user_read")
        self.assertEqual(entry["retrieval_status"], "content_retrieved")
        self.assertEqual(entry["url_provenance"], "cli_response")
        self.assertRegex(entry["content_sha256"], r"^[0-9a-f]{64}$")

        candidate_path = self.staging / result["candidate"]
        candidate = candidate_path.read_text(encoding="utf-8")
        self.assertIn("source_refs:", candidate)
        self.assertIn(f'  - "{SOURCE_ID}"', candidate)
        self.assertIn("url: \"https://team.feishu.cn/minutes/", candidate)
        self.assertIn("review_status: unreviewed", candidate)
        self.assertIn("classification_status: pending", candidate)
        self.assertIn("source_content_trust: untrusted_data", candidate)
        self.assertIn("We agreed to ship the first slice.", candidate)

        transcript = self.staging / entry["transcript_file"]
        for directory in (self.staging, self.staging / ".source", self.staging / "candidates"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for file_path in (manifest_path, candidate_path, transcript):
            self.assertEqual(stat.S_IMODE(file_path.stat().st_mode), 0o600)

    def test_same_source_and_hash_is_idempotent_and_preserves_existing_candidate(self):
        runner = FixtureLarkCLI()
        first = self._ingest(runner)
        candidate_path = self.staging / first["candidate"]
        candidate_path.write_text("human-reviewed local candidate", encoding="utf-8")
        manifest_path = self.staging / "manifest.json"
        manifest_before = manifest_path.read_bytes()

        second = self._ingest(runner)

        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["version"], 1)
        self.assertEqual(second["content_sha256"], first["content_sha256"])
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(candidate_path.read_text(encoding="utf-8"), "human-reviewed local candidate")
        transcript = self.staging / json.loads(manifest_before)["entries"][0]["transcript_file"]
        self.assertEqual(transcript.read_text(encoding="utf-8"), runner.texts[0])
        self.assertEqual(list((self.staging / ".source").glob(".refresh-*")), [])

    def test_source_url_query_and_fragment_are_removed_before_persistence(self):
        query_secret = "access_token_fixture_secret"
        fragment_secret = "fragment_fixture_secret"
        unsafe_url = f"https://team.feishu.cn/minutes/{TOKEN}?token={query_secret}#auth={fragment_secret}"
        result = self._ingest(FixtureLarkCLI(url=unsafe_url))
        manifest_text = (self.staging / "manifest.json").read_text(encoding="utf-8")
        candidate_text = (self.staging / result["candidate"]).read_text(encoding="utf-8")

        self.assertIn(f"https://team.feishu.cn/minutes/{TOKEN}", manifest_text)
        self.assertNotIn(query_secret, manifest_text + candidate_text)
        self.assertNotIn(fragment_secret, manifest_text + candidate_text)
        self.assertNotIn("?token=", manifest_text + candidate_text)
        self.assertNotIn("#auth=", manifest_text + candidate_text)

    def test_existing_staging_directories_with_permissive_mode_fail_closed(self):
        cases = ("staging", ".source", "candidates")
        for target in cases:
            with self.subTest(target=target):
                stage = self.root / f"stage-{target.replace('.', 'dot')}"
                stage.mkdir(mode=0o700)
                if target == "staging":
                    target_path = stage
                else:
                    target_path = stage / target
                    target_path.mkdir(mode=0o700)
                target_path.chmod(0o755)
                runner = FixtureLarkCLI()
                with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
                    with self.assertRaisesRegex(ingest_module.IngestError, "permissions must be 0700"):
                        ingest_module.ingest(TOKEN, stage, lark_cli="fake-lark")
                self.assertEqual(runner.calls, [])

    def test_staging_or_managed_directory_symlink_fails_closed(self):
        external = self.root / "external"
        external.mkdir(mode=0o700)
        linked_stage = self.root / "linked-stage"
        linked_stage.symlink_to(external, target_is_directory=True)
        runner = FixtureLarkCLI()
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with self.assertRaisesRegex(ingest_module.IngestError, "symbolic links"):
                ingest_module.ingest(TOKEN, linked_stage, lark_cli="fake-lark")

        stage = self.root / "nested-links"
        stage.mkdir(mode=0o700)
        target = self.root / "source-target"
        target.mkdir(mode=0o700)
        (stage / ".source").symlink_to(target, target_is_directory=True)
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with self.assertRaisesRegex(ingest_module.IngestError, "symbolic links"):
                ingest_module.ingest(TOKEN, stage, lark_cli="fake-lark")
        self.assertEqual(runner.calls, [])

    def test_existing_staging_directory_with_other_owner_fails_closed(self):
        self.staging.mkdir(mode=0o700)
        runner = FixtureLarkCLI()
        with mock.patch.object(ingest_module.os, "geteuid", return_value=os.geteuid() + 1):
            with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
                with self.assertRaisesRegex(ingest_module.IngestError, "not owned by the current user"):
                    ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")
        self.assertEqual(runner.calls, [])

    @unittest.skipUnless(os.name == "posix", "flock is used on macOS/Linux")
    def test_staging_lock_competition_blocks_second_writer(self):
        self.staging.mkdir(mode=0o700)
        waiting = threading.Event()
        entered = threading.Event()

        def contender():
            waiting.set()
            with ingest_module._staging_lock(self.staging):
                entered.set()

        with ingest_module._staging_lock(self.staging):
            thread = threading.Thread(target=contender)
            thread.start()
            self.assertTrue(waiting.wait(1))
            time.sleep(0.1)
            self.assertFalse(entered.is_set())
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(entered.is_set())
        lock_path = self.staging / ".feishu-minutes-ingest.lock"
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "flock is used on macOS/Linux")
    def test_staging_lock_competes_across_processes(self):
        self.staging.mkdir(mode=0o700)
        started = self.root / "lock-child-started"
        entered = self.root / "lock-child-entered"
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import feishu_minutes_ingest as adapter\n"
            f"stage = Path({str(self.staging)!r})\n"
            f"Path({str(started)!r}).write_text('started')\n"
            "with adapter._staging_lock(stage):\n"
            f"    Path({str(entered)!r}).write_text('entered')\n"
        )
        with ingest_module._staging_lock(self.staging):
            child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 2
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not started.exists():
                child.terminate()
                stdout, stderr = child.communicate(timeout=2)
                self.fail(f"child process did not start: {stderr or stdout}")
            time.sleep(0.1)
            self.assertFalse(entered.exists(), "child acquired a lock held by the parent")
        stdout, stderr = child.communicate(timeout=3)
        self.assertEqual(child.returncode, 0, stderr or stdout)
        self.assertTrue(entered.exists())

    @unittest.skipUnless(os.name == "posix", "flock is used on macOS/Linux")
    def test_concurrent_imports_are_serialized_into_successive_revisions(self):
        first_fetch_entered = threading.Event()
        release_first_fetch = threading.Event()

        class PausedFixtureLarkCLI(FixtureLarkCLI):
            def __init__(self):
                super().__init__(texts=[
                    "Alice 00:00:01\nFirst serialized version.\n",
                    "Alice 00:00:01\nSecond serialized version.\n",
                ])
                self.fetch_number = 0
                self.calls_lock = threading.Lock()

            def __call__(self, command, cwd):
                result = super().__call__(command, cwd)
                if command[1:3] == ["minutes", "+detail"]:
                    with self.calls_lock:
                        self.fetch_number += 1
                        fetch_number = self.fetch_number
                    if fetch_number == 1:
                        first_fetch_entered.set()
                        if not release_first_fetch.wait(5):
                            raise AssertionError("test did not release the first import")
                return result

        runner = PausedFixtureLarkCLI()
        results = []
        errors = []

        def run_import():
            try:
                results.append(ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark"))
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=run_import)
        second = threading.Thread(target=run_import)
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            first.start()
            self.assertTrue(first_fetch_entered.wait(2))
            second.start()
            time.sleep(0.1)
            self.assertEqual(len(runner.calls), 2, "second process must wait before its auth check")
            release_first_fetch.set()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(result["version"] for result in results), [1, 2])
        manifest = json.loads((self.staging / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["entries"][0]["version"], 2)
        self.assertEqual(manifest["history"][0]["version"], 1)

    def test_updated_manifest_write_failure_restores_old_state_and_removes_candidate(self):
        runner = FixtureLarkCLI(texts=[
            "Alice 00:00:01\nOriginal transcript.\n",
            "Alice 00:00:01\nUpdated transcript.\n",
        ])
        self._ingest(runner)
        manifest_path = self.staging / "manifest.json"
        manifest_before = manifest_path.read_bytes()
        old_entry = json.loads(manifest_before)["entries"][0]

        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with mock.patch.object(ingest_module, "_write_json_atomic", side_effect=OSError("fixture manifest failure")):
                with self.assertRaisesRegex(ingest_module.IngestError, "could not write the source manifest"):
                    ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(len(list((self.staging / "candidates").glob("*.md"))), 1)
        old_transcript = self.staging / old_entry["transcript_file"]
        self.assertEqual(old_transcript.read_text(encoding="utf-8"), "Alice 00:00:01\nOriginal transcript.\n")
        self.assertEqual(list((self.staging / ".source").glob(".refresh-*")), [])
        failed = list((self.staging / ".source/failed-refresh").glob("transcript-*"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_text(encoding="utf-8"), "Alice 00:00:01\nUpdated transcript.\n")
        history_files = list((self.staging / ".source/history").rglob("*.txt"))
        self.assertEqual(history_files, [])

    def test_changed_transcript_increments_version_and_keeps_previous_source(self):
        runner = FixtureLarkCLI(texts=[
            "Alice 00:00:01\nOriginal transcript.\n",
            "Alice 00:00:01\nUpdated transcript.\n",
        ])
        first = self._ingest(runner)
        second = self._ingest(runner)

        self.assertEqual(second["status"], "updated")
        self.assertEqual(second["version"], 2)
        manifest = json.loads((self.staging / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(manifest["entries"][0]["supersedes"], f"{SOURCE_ID}@v1")
        self.assertEqual(manifest["entries"][0]["content_sha256"], second["content_sha256"])
        self.assertEqual(len(manifest["history"]), 1)
        previous = manifest["history"][0]
        self.assertEqual(previous["revision_id"], f"{SOURCE_ID}@v1")
        self.assertEqual(previous["content_sha256"], first["content_sha256"])
        self.assertTrue((self.staging / previous["transcript_file"]).is_file())
        self.assertEqual((self.staging / previous["transcript_file"]).read_text(encoding="utf-8"), "Alice 00:00:01\nOriginal transcript.\n")
        self.assertEqual(len(list((self.staging / "candidates").glob("*.md"))), 2)
        current = self.staging / manifest["entries"][0]["transcript_file"]
        self.assertEqual(current.read_text(encoding="utf-8"), "Alice 00:00:01\nUpdated transcript.\n")

    def test_candidate_write_failure_restores_previous_transcript_and_cleans_refresh(self):
        runner = FixtureLarkCLI(texts=[
            "Alice 00:00:01\nOriginal transcript.\n",
            "Alice 00:00:01\nUpdated transcript.\n",
        ])
        first = self._ingest(runner)
        manifest_path = self.staging / "manifest.json"
        manifest_before = manifest_path.read_bytes()
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with mock.patch.object(ingest_module, "_ensure_candidate", side_effect=ingest_module.IngestError("fixture candidate failure")):
                with self.assertRaisesRegex(ingest_module.IngestError, "fixture candidate failure"):
                    ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        original = self.staging / json.loads(manifest_before)["entries"][0]["transcript_file"]
        self.assertEqual(original.read_text(encoding="utf-8"), "Alice 00:00:01\nOriginal transcript.\n")
        self.assertEqual(len(list((self.staging / "candidates").glob("*.md"))), 1)
        self.assertEqual(list((self.staging / ".source").glob(".refresh-*")), [])
        failed = list((self.staging / ".source/failed-refresh").glob("transcript-*"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_text(encoding="utf-8"), "Alice 00:00:01\nUpdated transcript.\n")

    def test_finish_backup_failure_cleans_candidate_and_restores_previous_transcript(self):
        runner = FixtureLarkCLI(texts=[
            "Alice 00:00:01\nOriginal transcript.\n",
            "Alice 00:00:01\nUpdated transcript.\n",
        ])
        self._ingest(runner)
        manifest_path = self.staging / "manifest.json"
        manifest_before = manifest_path.read_bytes()
        previous = json.loads(manifest_before)["entries"][0]
        source_key = ingest_module.hashlib.sha256(SOURCE_ID.encode("utf-8")).hexdigest()[:20]
        history_dir = self.staging / ".source" / "history" / source_key
        history_dir.mkdir(mode=0o700, parents=True)
        conflict = history_dir / f"v1-{previous['content_sha256']}.txt"
        conflict.write_text("conflicting history fixture", encoding="utf-8")

        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with self.assertRaisesRegex(ingest_module.IngestError, "history path exists"):
                ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        original = self.staging / previous["transcript_file"]
        self.assertEqual(original.read_text(encoding="utf-8"), "Alice 00:00:01\nOriginal transcript.\n")
        self.assertEqual(conflict.read_text(encoding="utf-8"), "conflicting history fixture")
        self.assertEqual(len(list((self.staging / "candidates").glob("*.md"))), 1)
        self.assertEqual(list((self.staging / ".source").glob(".refresh-*")), [])
        failed = list((self.staging / ".source/failed-refresh").glob("transcript-*"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_text(encoding="utf-8"), "Alice 00:00:01\nUpdated transcript.\n")

    def test_transcript_path_outside_staging_is_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("must not be read", encoding="utf-8")
        runner = FixtureLarkCLI(transcript_path=str(outside))
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with mock.patch.object(ingest_module.parser_adapter, "parse", side_effect=AssertionError("must not parse")):
                with self.assertRaisesRegex(ingest_module.IngestError, "outside the staging source directory"):
                    ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")
        self.assertEqual(outside.read_text(encoding="utf-8"), "must not be read")

    def test_missing_scope_stops_before_transcript_command(self):
        runner = FixtureLarkCLI(scope_ok=False)
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with self.assertRaisesRegex(ingest_module.IngestError, "scope check failed"):
                ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")
        self.assertEqual(len(runner.calls), 1)
        self.assertFalse((self.staging / "manifest.json").exists())

    def test_constructed_url_fallback_is_marked_in_candidate_and_manifest(self):
        result = self._ingest(FixtureLarkCLI(url=None))
        manifest = json.loads((self.staging / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entries"][0]
        candidate = (self.staging / result["candidate"]).read_text(encoding="utf-8")
        self.assertEqual(entry["url_provenance"], "constructed_locator")
        self.assertIn("url_provenance: \"constructed_locator\"", candidate)
        self.assertIn("source url provenance: constructed_locator", candidate)

    @unittest.skipUnless(os.name == "posix", "POSIX subprocess umask only")
    def test_cli_child_gets_private_umask(self):
        captured = {}

        def fake_run(command, **options):
            captured["command"] = command
            captured.update(options)
            return CompletedProcess(command, 0, "{}", "")

        with mock.patch.object(ingest_module.subprocess, "run", side_effect=fake_run):
            ingest_module._run_cli(["fake-lark", "auth", "check"], self.root)

        self.assertEqual(captured["umask"], 0o077)
        self.assertEqual(captured["cwd"], str(self.root))
        self.assertTrue(captured["capture_output"])

    def test_cli_failure_does_not_expose_raw_cli_output(self):
        runner = FixtureLarkCLI(fetch_returncode=1)
        with mock.patch.object(ingest_module, "_run_cli", side_effect=runner):
            with self.assertRaises(ingest_module.IngestError) as caught:
                ingest_module.ingest(TOKEN, self.staging, lark_cli="fake-lark")
        self.assertNotIn("sensitive raw cli output", str(caught.exception))
        self.assertFalse((self.staging / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
