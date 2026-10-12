from __future__ import annotations

import threading
import unittest

from ladies_v9.background import BackgroundTasks


class BackgroundTasksTests(unittest.TestCase):
    def test_same_key_reuses_inflight_future(self):
        tasks = BackgroundTasks(max_workers=1)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def work(value):
            calls.append(value)
            started.set()
            release.wait(timeout=2)
            return value

        first = tasks.submit("rehydrate:1", work, 1)
        self.assertIsNotNone(first)
        self.assertTrue(started.wait(timeout=2))
        duplicate = tasks.submit("rehydrate:1", work, 2)
        self.assertIs(duplicate, first)

        release.set()
        self.assertEqual(first.result(timeout=2), 1)
        self.assertEqual(calls, [1])
        tasks.shutdown(wait=True)

    def test_shutdown_rejects_new_work(self):
        tasks = BackgroundTasks(max_workers=1)
        tasks.shutdown(wait=True)
        self.assertIsNone(tasks.submit("after-close", lambda: None))


if __name__ == "__main__":
    unittest.main()
