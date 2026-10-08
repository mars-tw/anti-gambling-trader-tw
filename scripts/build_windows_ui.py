"""Build the Windows onedir UI and a relocatable ZIP with PyInstaller.

This script only writes beneath the explicitly selected output directory.  It
does not install dependencies, touch user configuration, or build a one-file
executable that would unpack into opaque runtime locations.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path


APP_NAME = "AntiGamblingTrader"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Windows beginner UI bundle")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist"),
        help="Owned build output directory (default: dist)",
    )
    return parser


def _data_arg(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def build(output: Path) -> tuple[Path, Path]:
    if sys.platform != "win32":
        raise RuntimeError("This portable desktop bundle must be built on Windows")

    try:
        import PyInstaller.__main__ as pyinstaller
        import webview  # noqa: F401 - verifies the desktop extra is installed
    except Exception as exc:
        raise RuntimeError(
            "Build dependencies are missing. Install the project with "
            "'.[desktop,excel]' and PyInstaller before running this script."
        ) from exc

    root = Path(__file__).resolve().parents[1]
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    entry = root / "scripts" / "ui_entry.py"
    static_dir = root / "core" / "ui" / "static"
    examples_dir = root / "core" / "examples"
    broker_base = root / "core" / "broker" / "base.py"
    broker_paper = root / "core" / "broker" / "paper.py"
    for required in (entry, static_dir, examples_dir, broker_base, broker_paper):
        if not required.exists():
            raise RuntimeError(f"Required build input is missing: {required}")

    work_path = output / "_pyinstaller_work"
    spec_path = output / "_pyinstaller_spec"
    args = [
        str(entry),
        "--name",
        APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--distpath",
        str(output),
        "--workpath",
        str(work_path),
        "--specpath",
        str(spec_path),
        "--paths",
        str(root),
        "--add-data",
        _data_arg(static_dir, "core/ui/static"),
        "--add-data",
        _data_arg(examples_dir, "core/examples"),
        # The scaffold generator reads these source files at runtime, so they
        # must remain physical files in addition to compiled modules.
        "--add-data",
        _data_arg(broker_base, "core/broker"),
        "--add-data",
        _data_arg(broker_paper, "core/broker"),
        # PyInstaller's package hooks collect WebView/pythonnet data.  The
        # _tkinter hidden import activates the built-in Tcl/Tk data hook for
        # the native mode chooser.
        "--collect-all",
        "webview",
        "--collect-all",
        "pythonnet",
        "--hidden-import",
        "clr_loader",
        "--hidden-import",
        "_tkinter",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "tkinter.ttk",
    ]
    pyinstaller.run(args)

    bundle = output / APP_NAME
    executable = bundle / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the expected executable: {executable}")

    archive = output / f"{APP_NAME}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                arcname = Path(bundle.name) / path.relative_to(bundle)
                zf.write(path, arcname.as_posix())
    return executable, archive


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        executable, archive = build(args.output)
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Executable: {executable}")
    print(f"Archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
