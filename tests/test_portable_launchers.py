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
    def test_unix_start_launcher_opens_single_manager_and_copies_runtime_files(self):
        text = (PACKAGING / "start.command").read_text(encoding="utf-8")
        for name in ("launcher_manager.py", "launcher_core.py", "launcher_manager.html"):
            self.assertIn(name, text)
        self.assertIn('SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"', text)
        self.assertIn('elif [ -f "$SCRIPT_DIR/../current" ]; then', text)
        self.assertIn('STATE_DIR="$ROOT/.state"', text)
        self.assertIn('.venv/bin/python', text)
        self.assertIn('UV_PYTHON_INSTALL_DIR="$ROOT/.runtime/python"', text)
        self.assertIn('uv python install 3.12', text)
        self.assertIn(' launch --root "$ROOT"', text)
        self.assertNotIn("portable_manager.py", text)
        self.assertNotIn("PYTHONPATH", text)

    def test_windows_start_launcher_opens_single_manager_and_copies_runtime_files(self):
        text = (PACKAGING / "start.cmd").read_text(encoding="utf-8")
        for name in ("launcher_manager.py", "launcher_core.py", "launcher_manager.html"):
            self.assertIn(name, text)
        self.assertIn('set "SCRIPT_DIR=%~dp0"', text)
        self.assertIn('if exist "%PARENT%current"', text)
        self.assertIn('set "STATE_DIR=%ROOT%.state"', text)
        self.assertIn('.venv\\Scripts\\python.exe', text)
        self.assertIn('UV_PYTHON_INSTALL_DIR=%ROOT%.runtime\\python', text)
        self.assertIn('uv python install 3.12', text)
        self.assertIn(' launch --root "%ROOT%"', text)
        self.assertNotIn("portable_manager.py", text)
        self.assertNotIn("PYTHONPATH", text)

    def test_update_launchers_delegate_to_central_manager(self):
        unix = (PACKAGING / "update.command").read_text(encoding="utf-8")
        windows = (PACKAGING / "update.cmd").read_text(encoding="utf-8")
        self.assertIn('exec "$ROOT/start.command"', unix)
        self.assertIn('call "%ROOT%start.cmd"', windows)
        self.assertNotIn("portable_manager.py", unix)
        self.assertNotIn("portable_manager.py", windows)

    def test_installers_use_launcher_manager_and_uv_bootstrap(self):
        unix = (PACKAGING / "install.command").read_text(encoding="utf-8")
        windows = (PACKAGING / "install.cmd").read_text(encoding="utf-8")
        self.assertIn("tools/launcher_manager.py", unix)
        self.assertIn("tools\\launcher_manager.py", windows)
        self.assertIn('uv run --locked --project "$SOURCE" --python 3.12', unix)
        self.assertIn('uv run --locked --project "%SOURCE%" --python 3.12', windows)
        self.assertNotIn("portable_manager.py", unix)
        self.assertNotIn("portable_manager.py", windows)

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_prefers_existing_release_venv_and_copies_manager_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.8.0"
            tools = release / "tools"
            python = release / ".venv" / "bin" / "python"
            tools.mkdir(parents=True)
            python.parent.mkdir(parents=True)
            (tools / "launcher_manager.py").write_text("# manager\n", encoding="utf-8")
            (tools / "launcher_core.py").write_text("# core\n", encoding="utf-8")
            (tools / "launcher_manager.html").write_text("<html></html>\n", encoding="utf-8")
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
            state = root / ".state"
            for name in ("launcher_manager.py", "launcher_core.py", "launcher_manager.html"):
                self.assertTrue((state / name).is_file())
            args = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [str(state / "launcher_manager.py"), "launch", "--root", str(root)])

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_packaging_start_launcher_resolves_installed_parent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            packaging = root / "packaging"
            release = root / "releases" / "v9.8.2"
            tools = release / "tools"
            python = release / ".venv" / "bin" / "python"
            packaging.mkdir(parents=True)
            tools.mkdir(parents=True)
            python.parent.mkdir(parents=True)
            (tools / "launcher_manager.py").write_text("# manager\n", encoding="utf-8")
            (tools / "launcher_core.py").write_text("# core\n", encoding="utf-8")
            (tools / "launcher_manager.html").write_text("<html></html>\n", encoding="utf-8")
            (root / "current").write_text("v9.8.2\n", encoding="utf-8")
            shutil.copy2(PACKAGING / "start.command", packaging / "start.command")
            capture = root / "capture.txt"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
            python.chmod(0o755)
            env = os.environ.copy()
            env["CAPTURE"] = str(capture)

            result = subprocess.run(
                ["sh", str(packaging / "start.command")], env=env, text=True, capture_output=True, check=False
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = root / ".state"
            args = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [str(state / "launcher_manager.py"), "launch", "--root", str(root)])

    @unittest.skipIf(os.name == "nt", "POSIX launcher execution test")
    def test_start_launcher_uv_fallback_uses_install_local_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.8.0"
            tools = release / "tools"
            tools.mkdir(parents=True)
            (tools / "launcher_manager.py").write_text("# manager\n", encoding="utf-8")
            (tools / "launcher_core.py").write_text("# core\n", encoding="utf-8")
            (tools / "launcher_manager.html").write_text("<html></html>\n", encoding="utf-8")
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
