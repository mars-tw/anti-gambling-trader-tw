"""Frozen entry point for the beginner UI (safe with no attached console)."""

from __future__ import annotations

import importlib
import multiprocessing
import sys


def _data_api_worker(argv: list[str]) -> int:
    """Dispatch the frozen Shioaji worker without importing the UI launcher."""

    candidates = (
        ("core.data_api.shioaji_worker", "main"),
        ("core.data_api.worker", "main"),
        ("core.data_api.providers", "worker_main"),
    )
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise
        worker_main = getattr(module, attribute, None)
        if callable(worker_main):
            result = worker_main(argv)
            return int(result or 0)
    raise RuntimeError("The bundled read-only data worker is unavailable")


def main() -> int:
    multiprocessing.freeze_support()
    args = list(sys.argv[1:])
    if args and args[0] == "--data-api-worker":
        return _data_api_worker(args[1:])
    from core.ui.launcher import main as ui_main

    return ui_main()


if __name__ == "__main__":
    raise SystemExit(main())
