from __future__ import annotations

import threading
import unittest

from ladies_v9.background import BackgroundTasks


class BackgroundTaskTests(unittest.TestCase):
    def test_duplicate_key_is_not_scheduled_twice(self):
        tasks = BackgroundTasks(max_workers=1)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def work(value):
            calls.append(value)
            started.set()
            release.wait(timeout=2)
            return value

        first = tasks.submit(("rehydrate", 1), work, 1)
        self.assertIsNotNone(first)
        self.assertTrue(started.wait(timeout=2))
        duplicate = tasks.submit(("rehydrate", 1), work, 2)
        self.assertIsNone(duplicate)
        self.assertEqual(tasks.pending_count, 1)

        release.set()
        self.assertEqual(first.result(timeout=2), 1)
        self.assertEqual(calls, [1])
        tasks.shutdown()

    def test_different_keys_can_be_queued(self):
        tasks = BackgroundTasks(max_workers=1)
        release = threading.Event()

        def work():
            release.wait(timeout=2)

        first = tasks.submit("a", work)
        second = tasks.submit("b", work)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(tasks.pending_count, 2)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
        tasks.shutdown()


if __name__ == "__main__":
    unittest.main()
