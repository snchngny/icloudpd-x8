"""Portable entry point; account and destination are always explicit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def main(argv=None):
    parser = argparse.ArgumentParser(description="iCloud写真を最大8並列で直接保存するCLI")
    parser.add_argument("command", choices=["authenticate", "download", "run", "status", "stop"])
    parser.add_argument("--username", required=True, help="Apple Accountのメールアドレス")
    parser.add_argument("--directory", required=True, help="写真の保存先。NAS共有パスも指定可能")
    parser.add_argument("--state-dir", help="認証・位置・ログの保存先。公開しないこと")
    parser.add_argument("--workers", type=int, choices=[1, 2, 4, 6, 8], default=8)
    parser.add_argument("--recent", type=int, help="downloadのみ、直近の指定件数を取得")
    args = parser.parse_args(argv)
    identity = hashlib.sha256((args.username + "\n" + str(Path(args.directory).absolute())).encode()).hexdigest()[:16]
    root = Path(args.state_dir or str(Path(__file__).resolve().parent / ".local" / identity)).absolute()
    binding = {"account_hash": hashlib.sha256(args.username.encode()).hexdigest(), "destination": str(Path(args.directory).absolute())}
    root.mkdir(parents=True, exist_ok=True)
    binding_path = root / "binding.json"
    if binding_path.exists() and json.loads(binding_path.read_text()) != binding:
        parser.error("state-dirは別のアカウント・保存先で使用済み。別のフォルダを指定してください")
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    (root / "state" / "cookies").mkdir(parents=True, exist_ok=True)
    if args.command == "status":
        status = root / "state" / "download-status.json"
        print(status.read_text() if status.exists() else '{"status":"not_started"}')
        return 0
    if args.command == "stop":
        (root / "state" / "stop-download").touch()
        return 0
    if args.command == "run" and args.recent is not None:
        parser.error("runは全件取得専用。直近試験にはdownloadを使ってください")
    env = dict(os.environ, ICLOUD_PC_USERNAME=args.username, ICLOUD_PC_DESTINATION=str(Path(args.directory).absolute()), ICLOUD_PC_STATE_DIR=str(root), ICLOUD_PC_START_WORKERS=str(args.workers), ICLOUD_PC_WORKERS=str(args.workers), ICLOUD_PC_CHECKPOINT="0")
    source = Path(__file__).resolve().parent
    if args.command == "run":
        (root / "state" / "stop-download").unlink(missing_ok=True)
        return subprocess.call([sys.executable, str(source / "download_pc.py")], env=env)
    command = [sys.executable, str(source / "performance_cli.py"), "--username", args.username, "--cookie-directory", str(root / "state" / "cookies"), "--password-provider", "console", "--mfa-provider", "console"]
    if args.command == "authenticate":
        command.append("--auth-only")
    else:
        command.extend(["--directory", str(Path(args.directory).absolute()), "--size", "original", "--no-progress-bar"])
        if args.recent is not None:
            command.extend(["--recent", str(args.recent)])
    return subprocess.call(command, env=env)

if __name__ == "__main__":
    raise SystemExit(main())
