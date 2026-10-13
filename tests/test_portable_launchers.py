from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


class PortableLauncherContractTests(unittest.TestCase):
    def test_unix_launchers_use_release_venv_or_uv_managed_runtime(self):
        for name in ("start.command", "update.command"):
            text = (PACKAGING / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn('.venv/bin/python', text)
                self.assertIn('UV_PYTHON_INSTALL_DIR="$ROOT/.runtime/python"', text)
                self.assertIn('UV_PROJECT_ENVIRONMENT="$RELEASE/.venv"', text)
                self.assertIn('uv python install 3.12', text)
                self.assertIn('uv python find 3.12', text)
                self.assertIn('uv run --locked --project "$RELEASE"', text)
                self.assertNotIn('exec python3 ', text)

    def test_windows_launchers_use_release_venv_or_uv_managed_runtime(self):
        for name in ("start.cmd", "update.cmd"):
            text = (PACKAGING / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn('.venv\\Scripts\\python.exe', text)
                self.assertIn('UV_PYTHON_INSTALL_DIR=%ROOT%.runtime\\python', text)
                self.assertIn('UV_PROJECT_ENVIRONMENT=%RELEASE%\\.venv', text)
                self.assertIn('uv python install 3.12', text)
                self.assertIn('uv python find 3.12', text)
                self.assertIn('uv run --locked --project "%RELEASE%"', text)

    def test_installers_bootstrap_through_uv_instead_of_system_python(self):
        unix = (PACKAGING / "install.command").read_text(encoding="utf-8")
        windows = (PACKAGING / "install.cmd").read_text(encoding="utf-8")
        self.assertIn('uv run --locked --project "$SOURCE" --python 3.12', unix)
        self.assertNotIn('exec python3 ', unix)
        self.assertIn('uv run --locked --project "%SOURCE%" --python 3.12', windows)

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_prefers_existing_release_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.7.1"
            manager = release / "tools" / "portable_manager.py"
            python = release / ".venv" / "bin" / "python"
            manager.parent.mkdir(parents=True)
            python.parent.mkdir(parents=True)
            manager.write_text("# manager\n", encoding="utf-8")
            (root / "current").write_text("v9.7.1\n", encoding="utf-8")
            shutil.copy2(PACKAGING / "start.command", root / "start.command")
            capture = root / "capture.txt"
            python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
            env = os.environ.copy()
            env["CAPTURE"] = str(capture)

            result = subprocess.run(
                ["sh", str(root / "start.command"), "--port", "9876"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args[0], str(manager))
            self.assertEqual(args[1:4], ["start", "--root", str(root)])
            self.assertEqual(args[4:], ["--port", "9876"])

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_uv_fallback_uses_install_local_python_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.7.1"
            manager = release / "tools" / "portable_manager.py"
            manager.parent.mkdir(parents=True)
            manager.write_text("# manager\n", encoding="utf-8")
            (root / "current").write_text("v9.7.1\n", encoding="utf-8")
            shutil.copy2(PACKAGING / "start.command", root / "start.command")

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            capture = root / "uv-run.txt"
            env_capture = root / "uv-env.txt"
            uv = fake_bin / "uv"
            uv.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = python ] && [ \"$2\" = install ]; then exit 0; fi\n"
                "if [ \"$1\" = python ] && [ \"$2\" = find ]; then echo /managed/python3.12; exit 0; fi\n"
                "if [ \"$1\" = run ]; then\n"
                "  printf '%s\\n' \"$@\" > \"$CAPTURE\"\n"
                "  printf '%s\\n%s\\n' \"$UV_PYTHON_INSTALL_DIR\" \"$UV_PROJECT_ENVIRONMENT\" > \"$ENV_CAPTURE\"\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["CAPTURE"] = str(capture)
            env["ENV_CAPTURE"] = str(env_capture)

            result = subprocess.run(
                ["sh", str(root / "start.command")],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            uv_args = capture.read_text(encoding="utf-8").splitlines()
            self.assertIn("--python", uv_args)
            self.assertIn("/managed/python3.12", uv_args)
            runtime_dir, project_env = env_capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(runtime_dir, str(root / ".runtime" / "python"))
            self.assertEqual(project_env, str(release / ".venv"))

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_distribution_start_launcher_fails_with_install_guidance(self):
        result = subprocess.run(
            ["sh", str(PACKAGING / "start.command")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Portable Installation ist noch nicht eingerichtet", result.stderr)


if __name__ == "__main__":
    unittest.main()
