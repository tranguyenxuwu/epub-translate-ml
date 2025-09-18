#!/usr/bin/env python3
"""Launcher for the Tkinter-based EPUB Translator UI."""

from pathlib import Path
import sys


def main() -> int:
    script_dir = Path(__file__).parent.resolve()
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    try:
        from app import main as app_main
    except ImportError as exc:  # pragma: no cover - defensive
        print(f"❌ Unable to import Tkinter app: {exc}")
        return 1

    print("🚀 Starting EPUB Translator (Tkinter UI)…")
    print(f"📁 Working directory: {script_dir}")
    app_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
