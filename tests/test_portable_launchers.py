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
    def test_unix_start_launcher_opens_manager_with_embedded_python(self):
        text = (PACKAGING / "start.command").read_text(encoding="utf-8")
        self.assertIn('tools/launcher_manager.py', text)
        self.assertIn('STATE_DIR="$ROOT/.state"', text)
        self.assertIn('MANAGER="$STATE_DIR/launcher_manager.py"', text)
        self.assertIn('.venv/bin/python', text)
        self.assertIn('UV_PYTHON_INSTALL_DIR="$ROOT/.runtime/python"', text)
        self.assertIn('uv python install 3.12', text)
        self.assertIn('uv python find 3.12', text)
        self.assertIn(' launch --root "$ROOT"', text)
        self.assertNotIn('exec python3 ', text)
        self.assertNotIn('portable_manager.py" start', text)

    def test_windows_start_launcher_opens_manager_with_embedded_python(self):
        text = (PACKAGING / "start.cmd").read_text(encoding="utf-8")
        self.assertIn('tools\\launcher_manager.py', text)
        self.assertIn('set "STATE_DIR=%ROOT%.state"', text)
        self.assertIn('set "MANAGER=%STATE_DIR%\\launcher_manager.py"', text)
        self.assertIn('.venv\\Scripts\\python.exe', text)
        self.assertIn('UV_PYTHON_INSTALL_DIR=%ROOT%.runtime\\python', text)
        self.assertIn('uv python install 3.12', text)
        self.assertIn('uv python find 3.12', text)
        self.assertIn(' launch --root "%ROOT%"', text)
        self.assertNotIn('portable_manager.py" start', text)

    def test_update_launchers_delegate_to_central_manager(self):
        unix = (PACKAGING / "update.command").read_text(encoding="utf-8")
        windows = (PACKAGING / "update.cmd").read_text(encoding="utf-8")
        self.assertIn('exec "$ROOT/start.command"', unix)
        self.assertIn('call "%ROOT%start.cmd"', windows)
        self.assertNotIn('portable_manager.py', unix)
        self.assertNotIn('portable_manager.py', windows)

    def test_installers_bootstrap_through_uv_instead_of_system_python(self):
        unix = (PACKAGING / "install.command").read_text(encoding="utf-8")
        windows = (PACKAGING / "install.cmd").read_text(encoding="utf-8")
        self.assertIn('uv run --locked --project "$SOURCE" --python 3.12', unix)
        self.assertNotIn('exec python3 ', unix)
        self.assertIn('uv run --locked --project "%SOURCE%" --python 3.12', windows)

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_prefers_existing_release_venv_and_copies_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.8.0"
            source_manager = release / "tools" / "launcher_manager.py"
            python = release / ".venv" / "bin" / "python"
            source_manager.parent.mkdir(parents=True)
            python.parent.mkdir(parents=True)
            source_manager.write_text("# manager\n", encoding="utf-8")
            (root / "current").write_text("v9.8.0\n", encoding="utf-8")
            shutil.copy2(PACKAGING / "start.command", root / "start.command")
            capture = root / "capture.txt"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
            python.chmod(0o755)
            env = os.environ.copy()
            env["CAPTURE"] = str(capture)

            result = subprocess.run(
                ["sh", str(root / "start.command")], env=env, text=True, capture_output=True, check=False
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            manager = root / ".state" / "launcher_manager.py"
            self.assertTrue(manager.is_file())
            args = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [str(manager), "launch", "--root", str(root)])

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_uv_fallback_uses_install_local_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.8.0"
            manager = release / "tools" / "launcher_manager.py"
            manager.parent.mkdir(parents=True)
            manager.write_text("# manager\n", encoding="utf-8")
            (root / "current").write_text("v9.8.0\n", encoding="utf-8")
            shutil.copy2(PACKAGING / "start.command", root / "start.command")

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            capture = root / "python-run.txt"
            uv = fake_bin / "uv"
            managed_python = fake_bin / "managed-python"
            managed_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
            managed_python.chmod(0o755)
            uv.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = python ] && [ \"$2\" = install ]; then exit 0; fi\n"
                "if [ \"$1\" = python ] && [ \"$2\" = find ]; then echo \"$MANAGED_PYTHON\"; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["CAPTURE"] = str(capture)
            env["MANAGED_PYTHON"] = str(managed_python)

            result = subprocess.run(
                ["sh", str(root / "start.command")], env=env, text=True, capture_output=True, check=False
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args[0], str(root / ".state" / "launcher_manager.py"))
            self.assertEqual(args[1:], ["launch", "--root", str(root)])

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_distribution_start_launcher_fails_with_install_guidance(self):
        result = subprocess.run(
            ["sh", str(PACKAGING / "start.command")], cwd=ROOT, text=True, capture_output=True, check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Portable Installation ist noch nicht eingerichtet", result.stderr)


if __name__ == "__main__":
    unittest.main()
