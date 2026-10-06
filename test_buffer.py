import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import icloudpd.download as patched

from performance_cli import install_buffer
install_buffer()
created = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

class Response:
    def __init__(self, data, fail=False):
        self.data, self.fail, self.calls = data, fail, 0
    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            self.calls += 1
            yield self.data[offset:offset + chunk_size]
            if self.fail:
                raise ConnectionError("synthetic disconnect")

class TransferTests(unittest.TestCase):
    def test_exact_bytes_and_timestamp(self):
        data = bytes(range(256)) * 2051
        with tempfile.TemporaryDirectory(prefix="icloud-buffer-test-") as tmp:
            target, partial = Path(tmp) / "result", Path(tmp) / "partial"
            patched.download_response_to_path(Response(data), str(partial), False, str(target), created)
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(partial.exists())
            self.assertAlmostEqual(target.stat().st_mtime, created.timestamp(), delta=2)
    def test_resume_preserves_prefix(self):
        with tempfile.TemporaryDirectory(prefix="icloud-buffer-test-") as tmp:
            target, partial = Path(tmp) / "result", Path(tmp) / "partial"
            partial.write_bytes(b"prefix")
            patched.download_response_to_path(Response(b"suffix"), str(partial), True, str(target), created)
            self.assertEqual(target.read_bytes(), b"prefixsuffix")
    def test_disconnect_does_not_publish_partial(self):
        with tempfile.TemporaryDirectory(prefix="icloud-buffer-test-") as tmp:
            target, partial = Path(tmp) / "result", Path(tmp) / "partial"
            with self.assertRaises(ConnectionError):
                patched.download_response_to_path(Response(b"x" * 600000, True), str(partial), False, str(target), created)
            self.assertFalse(target.exists())
            self.assertEqual(partial.stat().st_size, 256 * 1024)

if __name__ == '__main__':
    unittest.main()
