import logging
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
import requests
import base64
import io
import performance_cli as runtime

class ParallelTests(unittest.TestCase):
    def setUp(self):
        runtime.WORKERS = 4
        self.logger = logging.getLogger("parallel-test")
        self.service = SimpleNamespace(photos=SimpleNamespace(session=requests.Session()))

    def test_parallel_and_collision_serialization(self):
        lock = threading.Lock()
        active, maximum, names = 0, 0, set()
        def download(service, counter, photo):
            nonlocal active, maximum
            key = Path(photo.filename).stem
            with lock:
                self.assertNotIn(key, names)
                names.add(key)
                active += 1
                maximum = max(maximum, active)
            time.sleep(.03)
            with lock:
                active -= 1
                names.remove(key)
            return True
        pool = runtime.Downloads(download, self.service, self.logger)
        for name in ("a.HEIC", "a.MOV", "b.JPG", "c.JPG", "d.JPG"):
            pool.submit(SimpleNamespace(filename=name))
        pool.finish()
        self.assertGreater(maximum, 1)
        self.assertEqual(active, 0)

    def test_worker_exception_never_completes(self):
        def download(*args):
            raise IOError("simulated failure")
        pool = runtime.Downloads(download, self.service, self.logger)
        pool.submit(SimpleNamespace(filename="a.JPG"))
        with self.assertRaises(IOError):
            pool.finish()
        self.assertTrue(pool.closed)

    def test_same_checksum_different_names_are_serial(self):
        active = threading.Event()
        def download(service, counter, photo):
            self.assertFalse(active.is_set())
            active.set()
            time.sleep(.02)
            active.clear()
            return True
        pool = runtime.Downloads(download, self.service, self.logger)
        for name in ("first.JPG", "second.JPG"):
            pool.submit(SimpleNamespace(filename=name, versions={"original": SimpleNamespace(checksum="same-content")}))
        pool.finish()

    def test_cache_does_not_hide_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = runtime.ExistingFiles()
            path = Path(directory) / "new.JPG"
            self.assertFalse(cache.isfile(path))
            path.write_bytes(b"complete")
            self.assertTrue(cache.isfile(path))
            self.assertEqual(cache.stat(path).st_size, 8)

    def test_parallel_resumes_partial_download(self):
        from datetime import datetime, timezone
        from icloudpd.download import download_media
        from pyicloud_ipd.version_size import AssetVersionSize
        with tempfile.TemporaryDirectory() as directory:
            checksum = b"resume-test"
            part = Path(directory) / (base64.b32encode(checksum).decode() + ".part")
            part.write_bytes(b"abc")
            target = Path(directory) / "a.JPG"
            offsets = []
            def response(session, url, start):
                offsets.append(start)
                result = requests.Response()
                result.status_code = 206
                result.raw = io.BytesIO(b"abcdef"[start:])
                return result
            photo = SimpleNamespace(filename="a.JPG", created=datetime.now(timezone.utc), download=response)
            version = SimpleNamespace(url="https://example.invalid/asset", checksum=base64.b64encode(checksum))
            def download(service, counter, item):
                return download_media(self.logger, False, service, item, str(target), version, AssetVersionSize.ORIGINAL, lambda p: p.filename)
            pool = runtime.Downloads(download, self.service, self.logger)
            pool.submit(photo)
            pool.finish()
            self.assertEqual(offsets, [3])
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertFalse(part.exists())

if __name__ == "__main__":
    unittest.main()
