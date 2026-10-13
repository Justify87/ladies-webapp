from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import launcher_core as core
from tools import launcher_manager as lm


ROOT = Path(__file__).resolve().parents[1]


class ManagerLifecycleRegressionTests(unittest.TestCase):
    def test_untracked_ladies_listener_is_adopted_into_pid_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            root.mkdir()
            with mock.patch.object(core, "active_pid", return_value=None), mock.patch.object(
                lm, "_listener_pids", return_value=[4321]
            ), mock.patch.object(lm, "_looks_like_ladies_process", return_value=True):
                pid = lm._managed_app_pid(root)
            self.assertEqual(pid, 4321)
            self.assertEqual(core._read_pid(core._pid_path(root)), 4321)

    def test_graceful_stop_waits_for_process_and_port_before_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            root.mkdir()
            messages: list[str] = []
            with mock.patch.object(lm, "_managed_app_pid", return_value=222), mock.patch.object(
                lm, "_terminate_pid_tree"
            ) as terminate, mock.patch.object(lm, "_wait_until_stopped", return_value=True) as wait:
                stopped = lm.stop_app(root, messages.append)
            self.assertTrue(stopped)
            terminate.assert_called_once_with(222, force=False)
            wait.assert_called_once_with(222, lm.STOP_GRACE_TIMEOUT)
            self.assertIn("Port ist frei", messages[-1])

    def test_stop_escalates_to_force_only_after_grace_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            root.mkdir()
            with mock.patch.object(lm, "_managed_app_pid", return_value=222), mock.patch.object(
                lm, "_wait_until_stopped", side_effect=[False, True]
            ), mock.patch.object(lm, "_listener_pids", return_value=[222, 333]), mock.patch.object(
                lm, "_looks_like_ladies_process", return_value=True
            ), mock.patch.object(lm, "_terminate_pid_tree") as terminate:
                self.assertTrue(lm.stop_app(root))
            self.assertIn(mock.call(222, force=False), terminate.mock_calls)
            self.assertIn(mock.call(222, force=True), terminate.mock_calls)
            self.assertIn(mock.call(333, force=True), terminate.mock_calls)

    def test_stop_refuses_to_kill_unknown_process_using_app_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            root.mkdir()
            with mock.patch.object(lm, "_managed_app_pid", return_value=None), mock.patch.object(
                lm, "_port_listening", return_value=True
            ), mock.patch.object(lm, "_listener_pids", return_value=[999]), mock.patch.object(
                lm, "_terminate_pid_tree"
            ) as terminate:
                with self.assertRaises(core.PortableError) as failure:
                    lm.stop_app(root)
            self.assertIn("gehört aber nicht erkennbar zu Ladies Explorer", str(failure.exception))
            terminate.assert_not_called()

    def test_start_reuses_recovered_process_instead_of_reporting_port_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            root.mkdir()
            with mock.patch.object(lm, "_managed_app_pid", return_value=222), mock.patch.object(
                lm.webbrowser, "open"
            ) as browser, mock.patch.object(lm, "_port_available") as port_available:
                self.assertEqual(lm.start_app(root), 222)
            browser.assert_called_once_with(lm._app_url(), new=2)
            port_available.assert_not_called()

    def test_runtime_prepare_moves_node_setup_out_of_first_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            release = root / "releases" / "v9.8.4"
            release.mkdir(parents=True)
            python = release / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            with mock.patch.object(lm, "ensure_release_environment", return_value=python), mock.patch.object(
                lm.subprocess, "run"
            ) as run:
                result = lm.prepare_app_runtime(root, "v9.8.4")
            self.assertEqual(result, python)
            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(python), "-c"])
            self.assertIn("ensure_node_runtime", command[2])
            self.assertIn("ensure_javascript_dependencies", command[2])

    def test_manager_ui_has_inline_markdown_notes_and_no_update_confirmation(self):
        html = (ROOT / "tools" / "launcher_manager.html").read_text(encoding="utf-8")
        self.assertIn('id="notesDialog"', html)
        self.assertIn("renderMarkdown", html)
        self.assertIn("Was ist neu?", html)
        self.assertIn("Sicherungspunkt anlegen", html)
        self.assertNotIn("confirm('Update", html)
        self.assertNotIn('id="releaseNotes"', html)


if __name__ == "__main__":
    unittest.main()
