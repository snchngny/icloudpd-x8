"""Bounded parallel downloader; upstream filename and resume rules stay in use."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque, OrderedDict
from pathlib import Path
from types import SimpleNamespace
import copy
import inspect
import logging
import os
import threading
import requests
import time
import json
from contextlib import ExitStack

WORKERS = int(os.environ.get("ICLOUD_PC_WORKERS", "1"))
POOLS = []
CHECKPOINT = Path(os.environ.get("ICLOUD_PC_STATE_DIR", ".local")) / "state" / "listing-checkpoint.json"

def install_buffer():
    import icloudpd.download as download
    if getattr(download, "_x8_buffer_installed", False):
        return
    source = inspect.getsource(download.download_response_to_path)
    old = "response.iter_content(chunk_size=1024)"
    if source.count(old) != 1:
        if "chunk_size=256 * 1024" in source:
            download._x8_buffer_installed = True
            return
        raise RuntimeError("Unexpected upstream buffer implementation")
    exec(compile(source.replace(old, "response.iter_content(chunk_size=256 * 1024)"), "<buffered-download>", "exec"), download.__dict__)
    download._x8_buffer_installed = True

def save_checkpoint(offset, pool, path):
    pool.drain()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    temporary.replace(path)

class AssetSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (30, 120))
        response = super().request(method, url, **kwargs)
        if response.status_code == 429:
            logging.getLogger("icloudpd").error("ASSET_RATE_LIMIT HTTP 429")
        return response

class ExistingFiles:
    def __init__(self):
        self.lock = threading.Lock()
        self.folders = OrderedDict()

    def known(self, path):
        folder, name = os.path.split(os.fspath(path))
        with self.lock:
            if folder not in self.folders:
                try:
                    with os.scandir(folder) as entries:
                        self.folders[folder] = {e.name: e.stat() for e in entries if e.is_file()}
                except FileNotFoundError:
                    self.folders[folder] = {}
                if len(self.folders) > 256:
                    self.folders.popitem(last=False)
            self.folders.move_to_end(folder)
            return self.folders[folder].get(name)

    def isfile(self, path):
        return self.known(path) is not None or os.path.isfile(path)

    def stat(self, path, *args, **kwargs):
        known = self.known(path)
        return known if known is not None and not args and not kwargs else os.stat(path, *args, **kwargs)

class WorkerErrors(logging.Handler):
    def __init__(self, pool):
        super().__init__(logging.ERROR)
        self.pool = pool

    def emit(self, record):
        if threading.current_thread().name.startswith(self.pool.prefix):
            self.pool.failed = True

class Downloads:
    def __init__(self, function, icloud, logger):
        self.function = function
        self.session = icloud.photos.session
        self.prefix = f"icloud-transfer-{len(POOLS)}"
        self.executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix=self.prefix)
        self.pending = deque()
        self.local = threading.local()
        self.locks = {}
        self.lock = threading.Lock()
        self.failed = False
        self.logger = logger
        self.handler = WorkerErrors(self)
        logger.addHandler(self.handler)
        self.closed = False
        self.checked = 0
        self.started = time.monotonic()
        POOLS.append(self)

    def worker(self, photo):
        if not hasattr(self.local, "session"):
            session = AssetSession()
            session.headers.update(self.session.headers)
            session.cookies = copy.copy(self.session.cookies)
            session.proxies.update(self.session.proxies)
            session.verify = self.session.verify
            session.auth = self.session.auth
            self.local.session = session
        # Assets sharing a basename may share a Live Photo movie path.
        keys = {"name:" + Path(photo.filename).stem.casefold()}
        # Upstream partial filenames use checksum rather than the final filename.
        keys.update("checksum:" + str(version.checksum) for version in getattr(photo, "versions", {}).values())
        with self.lock:
            guards = [self.locks.setdefault(key, threading.Lock()) for key in sorted(keys)]
        with ExitStack() as stack:
            for guard in guards:
                stack.enter_context(guard)
            from icloudpd.counter import Counter
            def reauthenticate():
                raise RuntimeError("Session refresh requires restarting the download process")
            proxy = SimpleNamespace(photos=SimpleNamespace(session=self.local.session), authenticate=reauthenticate)
            return self.function(proxy, Counter(0), photo)

    def submit(self, photo):
        if len(self.pending) >= WORKERS * 2:
            self.pending.popleft().result()
        if self.failed:
            raise RuntimeError("Download worker reported an error")
        self.pending.append(self.executor.submit(self.worker, photo))
        self.checked += 1
        if self.checked % 1000 == 0:
            self.logger.info("Checked %s assets in %.1f seconds", self.checked, time.monotonic() - self.started)
        return False

    def finish(self):
        try:
            self.drain()
        finally:
            self.close()

    def drain(self):
        while self.pending:
            self.pending.popleft().result()
        if self.failed:
            raise RuntimeError("Download worker reported an error")

    def close(self):
        if not self.closed:
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.logger.removeHandler(self.handler)
            self.closed = True

def install():
    install_buffer()
    import icloudpd.base as base
    from pyicloud_ipd.services.photos import PhotoAlbum
    page_size = int(os.environ.get("ICLOUD_PC_PAGE_SIZE", "100"))
    if page_size not in (100, 500):
        raise RuntimeError("Unsupported page size")
    original_request = PhotoAlbum.photos_request
    def photos_request(album):
        album.page_size = page_size
        checkpoint_enabled = os.environ.get("ICLOUD_PC_CHECKPOINT") == "1"
        if checkpoint_enabled and not getattr(album, "_pc_initialized", False):
            album._pc_initialized = True
            if CHECKPOINT.exists():
                saved = json.loads(CHECKPOINT.read_text())
                album.offset = max(0, int(saved["offset"]))
                logging.getLogger("icloudpd").info("Resuming listing at offset %s; a final full verification is required", album.offset)
        elif checkpoint_enabled and album.offset > 0 and POOLS:
            # Persist only the boundary before the next page, after all queued work succeeds.
            save_checkpoint(album.offset, POOLS[-1], CHECKPOINT)
        started = time.monotonic()
        response = original_request(album)
        records = response.json().get("records", [])
        count = sum(r.get("recordType") == "CPLMaster" for r in records)
        logging.getLogger("icloudpd").info("Listed %s assets at offset %s in %.2f seconds (page size %s)", count, album.offset, time.monotonic() - started, page_size)
        return response
    PhotoAlbum.photos_request = photos_request
    source = inspect.getsource(base.core_single_run)
    replacements = [
        ('download_photo = partial(downloader, icloud)', 'download_pool = Downloads(downloader, icloud, logger)'),
        ('download_result = passer_result and download_photo(\n                                    consecutive_files_found, item\n                                )', 'download_result = passer_result and download_pool.submit(item)'),
        ('                        if global_config.only_print_filenames:', '                        download_pool.finish()\n\n                        if global_config.only_print_filenames:'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError("Unexpected upstream source layout")
        source = source.replace(old, new)
    base.Downloads = Downloads
    exec(compile(source, "<parallel-core>", "exec"), base.__dict__)
    if os.environ.get("ICLOUD_PC_CACHE", "1") == "1":
        cache = ExistingFiles()
        path_proxy = SimpleNamespace(**{n: getattr(os.path, n) for n in dir(os.path) if not n.startswith("__")})
        path_proxy.isfile = cache.isfile
        os_proxy = SimpleNamespace(**{n: getattr(os, n) for n in dir(os) if not n.startswith("__")})
        os_proxy.path = path_proxy
        os_proxy.stat = cache.stat
        base.os = os_proxy

if __name__ == "__main__":
    import sys
    forbidden = ("--auto-delete", "--delete-after-download", "--keep-icloud-recent-days", "--until-found", "--only-print-filenames", "--watch-with-interval")
    if WORKERS not in (1, 2, 4, 6, 8) or any(a.split("=")[0] in forbidden for a in sys.argv[1:]):
        raise SystemExit("Parallel trial supports download-only, 1/2/4/6/8 workers")
    if os.environ.get("ICLOUD_PC_CHECKPOINT") == "1":
        if "--album" in sys.argv or "--recent" in sys.argv:
            raise SystemExit("Checkpoint is limited to full-library transfers")
    try:
        install()
        from icloudpd.cli import cli
        cli()
    finally:
        for pool in POOLS:
            pool.close()
