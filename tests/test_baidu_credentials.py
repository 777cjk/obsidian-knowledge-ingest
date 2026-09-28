import os
import sys
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import baidu_credentials
import baidu_netdisk_credentials


class BaiduCredentialTests(unittest.TestCase):
    def test_environment_token_takes_precedence(self):
        with mock.patch.dict(os.environ, {"BAIDU_NETDISK_ACCESS_TOKEN": "fixture-token"}):
            with mock.patch.object(baidu_credentials.subprocess, "run") as run:
                self.assertEqual(baidu_credentials.get_access_token(), "fixture-token")
            run.assert_not_called()

    def test_keychain_token_is_read_without_echoing_it(self):
        completed = mock.Mock(returncode=0, stdout="fixture-token\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(baidu_credentials.sys, "platform", "darwin"):
                with mock.patch.object(baidu_credentials.shutil, "which", return_value="/usr/bin/security"):
                    with mock.patch.object(baidu_credentials.subprocess, "run", return_value=completed) as run:
                        self.assertEqual(baidu_credentials.get_access_token(), "fixture-token")

        command = run.call_args.args[0]
        self.assertIn("find-generic-password", command)
        self.assertEqual(command[-1], "-w")
        self.assertNotIn("fixture-token", " ".join(command))

    def test_keychain_error_is_sanitized(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="secret detail")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(baidu_credentials.sys, "platform", "darwin"):
                with mock.patch.object(baidu_credentials.shutil, "which", return_value="/usr/bin/security"):
                    with mock.patch.object(baidu_credentials.subprocess, "run", return_value=completed):
                        with self.assertRaises(baidu_credentials.CredentialError) as caught:
                            baidu_credentials.get_access_token()

        self.assertNotIn("secret detail", str(caught.exception))

    def test_preflight_checks_presence_without_reading_secret(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(baidu_credentials.sys, "platform", "darwin"):
                with mock.patch.object(baidu_credentials.shutil, "which", return_value="/usr/bin/security"):
                    with mock.patch.object(baidu_credentials.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(baidu_credentials.is_configured())

        self.assertNotIn("-w", run.call_args.args[0])

    def test_store_command_uses_native_prompt_without_token_argument(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(baidu_netdisk_credentials.sys, "platform", "darwin"):
            with mock.patch.object(baidu_netdisk_credentials.shutil, "which", return_value="/usr/bin/security"):
                with mock.patch.object(baidu_netdisk_credentials.getpass, "getuser", return_value="fixture-user"):
                    with mock.patch.object(baidu_netdisk_credentials.subprocess, "run", return_value=completed) as run:
                        self.assertEqual(baidu_netdisk_credentials.main(), 0)

        command = run.call_args.args[0]
        self.assertEqual(command[-1], "-w")
        self.assertNotIn("fixture-token", " ".join(command))


if __name__ == "__main__":
    unittest.main()
