#!/usr/bin/env python3
"""One-command start on macOS, Windows and Linux.

  python run.py              (Windows: py run.py)
  python run.py --port 8080 --no-browser

First run creates .venv and installs dependencies (needs internet once).
Later runs reinstall only if a requirements file changed.
"""

import hashlib
import os
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

MIN_PY = (3, 10)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQS = [ROOT / "website" / "requirements.txt", ROOT / "computer-vision" / "requirements.txt"]
STAMP = VENV / ".requirements.sha256"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env():
    if sys.version_info < MIN_PY:
        sys.exit(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required (this is {sys.version.split()[0]}). "
                 "Install it from https://www.python.org/downloads/")

    if not venv_python().exists():
        print("Creating virtual environment in .venv ...")
        try:
            venv.create(VENV, with_pip=True)
        except Exception as e:
            sys.exit(f"Could not create .venv ({e}). On Debian/Ubuntu: sudo apt install python3-venv")

    digest = hashlib.sha256(b"".join(p.read_bytes() for p in REQS)).hexdigest()
    if not STAMP.exists() or STAMP.read_text().strip() != digest:
        print("Installing dependencies (first run takes a minute) ...")
        py = str(venv_python())
        subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
        subprocess.check_call([py, "-m", "pip", "install", "-q", "-r", str(REQS[0])])
        STAMP.write_text(digest)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_until_up(url: str, timeout=60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    args = sys.argv[1:]
    open_browser = "--no-browser" not in args
    args = [a for a in args if a != "--no-browser"]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8000
    url = f"http://localhost:{port}"

    if port_in_use(port):
        print(f"Port {port} is already in use. If Pin Finder is already running, open {url}")
        print("Otherwise pick another port:  python run.py --port 8080")
        if open_browser:
            webbrowser.open(url)
        return

    ensure_env()
    proc = subprocess.Popen([str(venv_python()), str(ROOT / "website" / "server.py"), *args])
    try:
        if open_browser and wait_until_up(f"{url}/api/boards"):
            webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
