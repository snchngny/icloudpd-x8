"""Download directly to NAS with bounded runs and retry backoff."""
from pathlib import Path
from datetime import datetime
import ctypes
import json
import subprocess
import threading
import time
import os
import re

source_root = Path(__file__).resolve().parent
root = Path(os.environ.get("ICLOUD_PC_STATE_DIR", ".local"))
state = root / "state" / "download-status.json"
stop = root / "state" / "stop-download"
phase_path = root / "state" / "download-phase.json"
settings_path = root / "state" / "performance-settings.json"
import sys
destination = Path(os.environ.get("ICLOUD_PC_DESTINATION", "."))
command = [sys.executable, str(source_root / "performance_cli.py"), "--username", os.environ.get("ICLOUD_PC_USERNAME", ""), "--directory", str(destination), "--cookie-directory", str(root / "state" / "cookies"), "--size", "original", "--password-provider", "console", "--mfa-provider", "console", "--no-progress-bar"]

def status(value, **details):
    state.write_text(json.dumps(dict(status=value, time=datetime.now().astimezone().isoformat(), destination=str(destination), **details), indent=2), encoding="utf-8")

def collect(process, result):
    latest = root / "logs" / "download-latest.log"
    if latest.exists():
        latest.replace(root / "logs" / f"download-history-{time.time_ns()}.log")
    with latest.open("w", encoding="utf-8") as log:
        for line in process.stdout:
            if any(level in line for level in (" INFO ", " ERROR ", " WARNING ")):
                log.write(line)
                log.flush()
            if "All photos and videos have been downloaded" in line:
                result["completed"] = True
            if " ERROR " in line:
                result["errors"] = True
            if "ASSET_RATE_LIMIT HTTP 429" in line:
                result["throttled"] = True
            # Preserve exception type, without URLs, response payloads or authentication data.
            match = re.match(r"^([\w.]+(?:Error|Exception|Timeout)):", line)
            if match:
                log.write(f" ERROR Process exception type: {match.group(1)}\n")
                log.flush()
                result["errors"] = True

def run():
    (root / "logs").mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    delay = 1800
    attempt = 0
    preferred = json.loads(settings_path.read_text()).get("workers", 4) if settings_path.exists() else 4
    workers = int(os.environ.get("ICLOUD_PC_START_WORKERS", str(preferred)))
    if workers not in (1, 2, 4, 6, 8):
        raise ValueError("Unsupported initial worker count")
    verifying = phase_path.exists() and json.loads(phase_path.read_text()).get("phase") == "verification"
    while not stop.exists():
        attempt += 1
        status("running", attempt=attempt, workers=workers, phase="verification" if verifying else "transfer")
        process = subprocess.Popen(command, env=dict(os.environ, ICLOUD_PC_WORKERS=str(workers), ICLOUD_PC_CACHE="1", ICLOUD_PC_PAGE_SIZE="100", ICLOUD_PC_CHECKPOINT="0" if verifying else "1"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        result = {"completed": False, "errors": False, "throttled": False}
        reader = threading.Thread(target=collect, args=(process, result))
        reader.start()
        started = time.monotonic()
        while process.poll() is None:
            if stop.exists() or time.monotonic() - started >= 21600:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            time.sleep(2)
        code = process.wait()
        reader.join()
        if stop.exists():
            break
        if code == 0 and result["completed"] and not result["errors"]:
            if not verifying:
                verifying = True
                phase_path.write_text(json.dumps({"phase": "verification"}), encoding="utf-8")
                continue
            status("completed", attempt=attempt)
            return
        if time.monotonic() - started >= 600:
            delay = 1800
        if result["throttled"]:
            workers = max(n for n in (1, 2, 4, 6, 8) if n <= max(1, workers // 2))
        status("retry_wait", attempt=attempt, exit_code=code, retry_seconds=delay, next_workers=workers, rate_limited=result["throttled"])
        until = time.monotonic() + delay
        while time.monotonic() < until and not stop.exists():
            time.sleep(2)
        delay = min(delay * 2, 7200)
    status("stopped")

if __name__ == "__main__":
    if not os.environ.get("ICLOUD_PC_USERNAME") or not os.environ.get("ICLOUD_PC_DESTINATION"):
        raise SystemExit("Use cli.py run with --username and --directory")
    # Keep the computer awake only while this supervisor exists.
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        run()
    except Exception as error:
        status("failed", error=type(error).__name__)
        raise
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
