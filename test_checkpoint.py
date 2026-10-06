from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace
from performance_cli import save_checkpoint
from performance_cli import Downloads
from concurrent.futures import Future
from collections import deque
import threading

class CheckpointTest(unittest.TestCase):
    def test_failed_work_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text('{"offset": 100}')
            def failed():
                raise IOError("unfinished download")
            with self.assertRaises(IOError):
                save_checkpoint(200, SimpleNamespace(drain=failed), path)
            self.assertEqual(json.loads(path.read_text())["offset"], 100)
            save_checkpoint(200, SimpleNamespace(drain=lambda: None), path)
            self.assertEqual(json.loads(path.read_text())["offset"], 200)

    def test_out_of_order_completion_waits_for_every_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text('{"offset": 100}')
            futures = [Future() for _ in range(8)]
            for future in futures[1:]:
                future.set_result(True)
            pool = Downloads.__new__(Downloads)
            pool.pending = deque(futures)
            pool.failed = False
            started, finished = threading.Event(), threading.Event()
            def persist():
                started.set()
                save_checkpoint(200, pool, path)
                finished.set()
            thread = threading.Thread(target=persist)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(.05))
                self.assertEqual(json.loads(path.read_text())["offset"], 100)
            finally:
                futures[0].set_result(True)
                thread.join(2)
            self.assertTrue(finished.is_set())
            self.assertEqual(json.loads(path.read_text())["offset"], 200)

    def test_later_worker_failure_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text('{"offset": 100}')
            futures = [Future() for _ in range(8)]
            for future in futures[:-1]:
                future.set_result(True)
            futures[-1].set_exception(IOError("last worker failed"))
            pool = Downloads.__new__(Downloads)
            pool.pending = deque(futures)
            pool.failed = False
            with self.assertRaises(IOError):
                save_checkpoint(200, pool, path)
            self.assertEqual(json.loads(path.read_text())["offset"], 100)

if __name__ == "__main__":
    unittest.main()
