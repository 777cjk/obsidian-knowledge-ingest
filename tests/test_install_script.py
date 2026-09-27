import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class InstallScriptTests(unittest.TestCase):
    def _sandbox(self, root: Path, old_python: bool = False):
        repo = root / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/install.sh", scripts / "install.sh")
        for name in (
            "requirements-parser-lite.txt",
            "requirements-markitdown.txt",
            "requirements-liteparse.txt",
            "requirements-baidu-mcp.txt",
        ):
            shutil.copy2(ROOT / name, repo / name)

        verify = scripts / "verify.sh"
        verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verify.chmod(0o755)

        venv = root / "venv"
        python = venv / "bin/python"
        python.parent.mkdir(parents=True)
        if old_python:
            python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            python.chmod(0o755)
        else:
            python.symlink_to(sys.executable)

        pip_log = root / "pip-args.json"
        (root / "pip.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['PIP_ARGS_LOG']).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(root), env.get("PYTHONPATH", "")) if part
        )
        env["PIP_ARGS_LOG"] = str(pip_log)
        return repo, venv, pip_log, env

    def _run(self, root: Path, args, old_python: bool = False):
        repo, venv, pip_log, env = self._sandbox(root, old_python=old_python)
        result = subprocess.run(
            [
                str(repo / "scripts/install.sh"),
                "--python",
                sys.executable,
                "--venv",
                str(venv),
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        pip_args = json.loads(pip_log.read_text(encoding="utf-8")) if pip_log.exists() else None
        return result, pip_args, repo

    def test_parser_lite_uses_combined_pinned_requirements(self):
        with tempfile.TemporaryDirectory() as temp:
            result, pip_args, repo = self._run(Path(temp), ["--with", "parser-lite"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                pip_args,
                ["install", "-r", str(repo / "requirements-parser-lite.txt")],
            )

    def test_individual_parsers_and_heavy_options_preserve_offline_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            result, pip_args, repo = self._run(
                Path(temp),
                [
                    "--offline",
                    "--with",
                    "markitdown",
                    "--with",
                    "liteparse",
                    "--with",
                    "docling",
                    "--with",
                    "ocrmypdf",
                ],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                pip_args,
                [
                    "install",
                    "--no-index",
                    "-r",
                    str(repo / "requirements-markitdown.txt"),
                    "-r",
                    str(repo / "requirements-liteparse.txt"),
                    "docling",
                    "ocrmypdf",
                ],
            )

    def test_parser_packages_reject_python_below_310_before_pip(self):
        with tempfile.TemporaryDirectory() as temp:
            result, pip_args, _ = self._run(
                Path(temp), ["--with", "parser-lite"], old_python=True
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("require Python >= 3.10", result.stderr)
            self.assertIsNone(pip_args)

    def test_baidu_mcp_uses_pinned_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            result, pip_args, repo = self._run(Path(temp), ["--with", "baidu-mcp"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                pip_args,
                ["install", "-r", str(repo / "requirements-baidu-mcp.txt")],
            )


if __name__ == "__main__":
    unittest.main()
