"""Start the whole application for local development; Ctrl+C stops everything.

    .venv\\Scripts\\python scripts\\dev.py        (Windows)
    .venv/bin/python scripts/dev.py              (macOS, Linux)

Processes:
  Django API      http://127.0.0.1:8000   accounts, portfolio, Checkpoint 8 workflow, model runs
  Upload service  http://127.0.0.1:8001   api/main.py (FastAPI); only when ADAPTER_API_KEY is set
  Interface       http://127.0.0.1:5173   frontend/ (Vite); proxies /api to Django

Settings are read from the environment and from .env at the repository root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found. Install Node.js 20 or newer.")
        return 1
    if not (ROOT / "frontend" / "node_modules").is_dir():
        print("Run `npm ci` in frontend/ first.")
        return 1

    subprocess.run([PYTHON, "backend/manage.py", "migrate", "--noinput"], cwd=ROOT, check=True)
    commands = [("django", [PYTHON, "backend/manage.py", "runserver", "127.0.0.1:8000", "--noreload"])]
    if os.environ.get("ADAPTER_API_KEY"):
        commands.append(("upload", [PYTHON, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8001"]))
    else:
        print("ADAPTER_API_KEY is not set: the upload service is not started and File upload is unavailable.")
    commands.append(("frontend", [npm, "run", "dev"]))

    processes = []
    for name, command in commands:
        cwd = ROOT / "frontend" if name == "frontend" else ROOT
        processes.append((name, subprocess.Popen(command, cwd=cwd)))
        print(f"started {name} (pid {processes[-1][1].pid})")
    print("Open http://127.0.0.1:5173 when the interface reports it is ready. Ctrl+C stops everything.")
    try:
        while all(p.poll() is None for _, p in processes):
            time.sleep(1)
        for name, p in processes:
            if p.poll() is not None:
                print(f"{name} exited with code {p.returncode}; stopping the others.")
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in processes:
            if p.poll() is None:
                p.terminate()
        for _, p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
