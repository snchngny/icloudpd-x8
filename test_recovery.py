"""Verify a failed run retries at lower concurrency and does not claim success."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import download_pc as supervisor

class RecoveryTest(unittest.TestCase):
    def test_error_then_success(self):
        workers = []
        checkpoints = []
        class Process:
            def __init__(self, *args, **kwargs):
                workers.append(kwargs["env"]["ICLOUD_PC_WORKERS"])
                checkpoints.append(kwargs["env"]["ICLOUD_PC_CHECKPOINT"])
                self.code = 1 if len(workers) == 1 else 0
                self.stdout = iter([" INFO All photos and videos have been downloaded\n", getattr(test, "error_line", " ERROR ASSET_RATE_LIMIT HTTP 429\n")] if self.code else [" INFO All photos and videos have been downloaded\n"])
            def poll(self):
                return self.code
            def wait(self, **kwargs):
                return self.code
        test = self
        clock = iter(range(0, 100000, 1000))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            state = root / "status.json"
            with patch.object(supervisor, "root", root), patch.object(supervisor, "state", state), patch.object(supervisor, "phase_path", root / "phase.json"), patch.object(supervisor, "settings_path", root / "settings.json"), patch.object(supervisor, "stop", root / "stop"), patch.object(supervisor.subprocess, "Popen", Process), patch.object(supervisor.time, "monotonic", lambda: next(clock)), patch.object(supervisor.time, "sleep", lambda _: None):
                supervisor.run()
            self.assertEqual(workers, getattr(self, "expected_workers", ["4", "2", "2"]))
            self.assertEqual(checkpoints, ["1", "1", "0"])
            self.assertEqual(json.loads(state.read_text())["status"], "completed")
            self.assertEqual(json.loads(state.read_text())["attempt"], 3)

    def test_non_rate_error_preserves_concurrency(self):
        self.error_line = " ERROR simulated NAS error\n"
        self.expected_workers = ["4", "4", "4"]
        self.test_error_then_success()

if __name__ == "__main__":
    unittest.main()
