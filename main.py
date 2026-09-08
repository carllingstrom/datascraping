#!/usr/bin/env python3
"""Entry point: python main.py  |  python main.py chat  |  python main.py run plan.json"""

import sys

if sys.platform == "win32":
    # Some Windows consoles (older cmd.exe/PowerShell hosts, or MSYS/Git-Bash pty layers)
    # default to a legacy codepage like cp1252, which can't encode most Unicode the app
    # or the AI's own reply might print (arrows, em-dashes, accented names, bullets) and
    # crashes the whole process mid-print. Force UTF-8 for both the real console codepage
    # and Python's own stdio streams before anything else runs.
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 — best-effort; fall through to the stream-level fix below
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
