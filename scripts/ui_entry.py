"""Frozen entry point for the beginner UI (safe with no attached console)."""

from __future__ import annotations

import multiprocessing


def main() -> int:
    multiprocessing.freeze_support()
    from core.ui.launcher import main as ui_main

    return ui_main()


if __name__ == "__main__":
    raise SystemExit(main())
