"""Build the Windows onedir UI and a relocatable ZIP with PyInstaller.

This script only writes beneath the explicitly selected output directory.  It
does not install dependencies, touch user configuration, or build a one-file
executable that would unpack into opaque runtime locations.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import re
import stat
import sys
import zipfile
from pathlib import Path


APP_NAME = "AntiGamblingTrader"
_LICENSE_NAME_MARKERS = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")
_RUNTIME_DISTRIBUTIONS = (
    "pywebview",
    "pythonnet",
    "clr-loader",
    "cffi",
    "pycparser",
    "proxy-tools",
    "typing-extensions",
    "openpyxl",
    "et-xmlfile",
    "defusedxml",
)
_MAX_LICENSE_FILE_BYTES = 2 * 1024 * 1024
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DELIVERY_README = """反詐投資王 Windows 版

使用方式
1. 解壓縮後請保留整個 AntiGamblingTrader 資料夾，不要只複製 .exe。
2. 在 Windows 10/11 x64 雙擊 AntiGamblingTrader.exe，啟動時會先出現模式選擇（choose），可選原生桌面或瀏覽器模式。
3. 原生桌面模式需要已安裝 WebView2 Runtime；本包不內含 WebView2。若無法使用，請改選瀏覽器模式。
4. 下載的 CSV 請保留；下次啟動後重新載入即可延續檢視資料。
5. 本包只做本機分析與紙上／示範用途，不連接或存取真實券商（live broker）。

授權：本專案採 MIT License，詳見同資料夾的 LICENSE。第三方授權見 THIRD_PARTY_LICENSES\\NOTICE.txt。
"""


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


def _is_reparse_stat(file_stat: os.stat_result) -> bool:
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


def _is_safe_regular_file(path: Path, *, max_bytes: int | None) -> bool:
    try:
        file_stat = path.lstat()
    except (OSError, ValueError):
        return False
    if _is_reparse_stat(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        return False
    return max_bytes is None or 0 <= file_stat.st_size <= max_bytes


def _has_reparse_component(path: Path, stop: Path) -> bool:
    current = path
    while True:
        try:
            file_stat = current.lstat()
        except (OSError, ValueError):
            return True
        if _is_reparse_stat(file_stat):
            return True
        if current == stop:
            return False
        try:
            current.relative_to(stop)
        except ValueError:
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent


def _safe_component(value: str) -> str:
    if not value or "\x00" in value:
        raise RuntimeError("Unsafe delivery path component")
    component = Path(value)
    if component.is_absolute() or component.name != value or value in {".", ".."}:
        raise RuntimeError("Unsafe delivery path component")
    return value


def _ensure_output_directory(base: Path, parts: tuple[str, ...]) -> Path:
    current = base
    if not current.is_dir() or _has_reparse_component(current, current):
        raise RuntimeError(f"Unsafe delivery bundle directory: {current}")
    for raw_part in parts:
        part = _safe_component(raw_part)
        current = current / part
        if os.path.lexists(str(current)):
            if _is_reparse_stat(current.lstat()):
                raise RuntimeError(f"Reparse point in delivery path: {current}")
            if not current.is_dir():
                raise RuntimeError(f"Delivery path is not a directory: {current}")
        else:
            current.mkdir()
    return current


def _ensure_output_file(parent: Path, filename: str) -> Path:
    filename = _safe_component(filename)
    target = parent / filename
    if os.path.lexists(str(target)):
        file_stat = target.lstat()
        if _is_reparse_stat(file_stat):
            raise RuntimeError(f"Reparse point in delivery path: {target}")
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"Delivery path is not a regular file: {target}")
    return target


def _safe_package_relative(value: object) -> Path | None:
    raw = str(value).replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = tuple(part for part in raw.split("/") if part not in {"", "."})
    if not parts or any(part == ".." or ":" in part for part in parts):
        return None
    relative = Path(*parts)
    if relative.is_absolute() or relative.name in {"", ".", ".."}:
        return None
    return relative


def _is_license_path(relative: Path) -> bool:
    filename = relative.name.upper()
    return any(marker in filename for marker in _LICENSE_NAME_MARKERS)


def _safe_distribution_source(distribution: object, relative: Path) -> Path | None:
    try:
        distribution_root = Path(distribution.locate_file(Path("."))).absolute()
        candidate = Path(distribution.locate_file(relative)).absolute()
        candidate.relative_to(distribution_root)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if _has_reparse_component(candidate, distribution_root):
        return None
    if not _is_safe_regular_file(candidate, max_bytes=_MAX_LICENSE_FILE_BYTES):
        return None
    return candidate


def _one_line(
    value: object, *, fallback: str = "未提供", reject_paths: bool = False
) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    if reject_paths and (
        "/" in text
        or "\\" in text
        or "://" in text
        or re.match(r"^[A-Za-z]:", text)
    ):
        return fallback
    return text[:240]


def _license_metadata(distribution: object) -> str:
    try:
        metadata = distribution.metadata
        license_value = _one_line(metadata.get("License"), reject_paths=True)
        get_all = getattr(metadata, "get_all", None)
        raw_classifiers = get_all("Classifier") if get_all is not None else metadata.get("Classifier")
        if isinstance(raw_classifiers, str):
            raw_classifiers = [raw_classifiers]
        classifiers = [
            _one_line(item, reject_paths=True)
            for item in (raw_classifiers or [])
            if str(item).startswith("License ::")
        ]
    except (AttributeError, TypeError, ValueError):
        return "未提供"
    details = []
    if license_value != "未提供":
        details.append(f"License={license_value}")
    if classifiers:
        details.append("Classifier=" + " | ".join(classifiers))
    return "; ".join(details) or "未提供"


def _license_target(directory: Path, filename: str, content: bytes) -> Path:
    candidate = _ensure_output_file(directory, filename)
    if not os.path.lexists(str(candidate)):
        return candidate
    try:
        if candidate.read_bytes() == content:
            return candidate
    except (OSError, ValueError):
        pass
    source_name = Path(filename)
    stem = source_name.stem or source_name.name
    suffix = source_name.suffix
    for index in range(2, 1000):
        alternate = _ensure_output_file(directory, f"{stem}-{index}{suffix}")
        if not os.path.lexists(str(alternate)):
            return alternate
        try:
            if alternate.read_bytes() == content:
                return alternate
        except (OSError, ValueError):
            continue
    raise RuntimeError(f"Too many license filename collisions in {directory}")


def _copy_bounded_license(source: Path, directory: Path, filename: str | None = None) -> Path | None:
    if not _is_safe_regular_file(source, max_bytes=_MAX_LICENSE_FILE_BYTES):
        return None
    try:
        content = source.read_bytes()
    except (OSError, ValueError):
        return None
    if len(content) > _MAX_LICENSE_FILE_BYTES:
        return None
    target = _license_target(directory, filename or source.name, content)
    target.write_bytes(content)
    return target


def _collect_distribution_licenses(
    distribution_name: str, third_party_root: Path
) -> tuple[str, str, str, tuple[str, ...]] | None:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None

    try:
        name = _one_line(distribution.metadata.get("Name"), fallback=distribution_name)
        version = _one_line(getattr(distribution, "version", ""))
        metadata_license = _license_metadata(distribution)
        entries = distribution.files or ()
    except (AttributeError, TypeError, ValueError):
        return None

    collected: list[str] = []
    destination: Path | None = None
    seen: set[str] = set()
    for entry in sorted(entries, key=lambda item: str(item).lower()):
        relative = _safe_package_relative(entry)
        if relative is None or not _is_license_path(relative):
            continue
        relative_key = relative.as_posix()
        if relative_key in seen:
            continue
        seen.add(relative_key)
        source = _safe_distribution_source(distribution, relative)
        if source is None:
            continue
        if destination is None:
            destination = _ensure_output_directory(
                third_party_root, (_safe_component(_safe_distribution_name(name)),)
            )
        target = _copy_bounded_license(source, destination, relative.name)
        if target is not None:
            collected.append(target.name)

    return name, version, metadata_license, tuple(dict.fromkeys(collected))


def _safe_distribution_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    safe = safe.strip("._")
    return safe or "unknown-distribution"


def _write_inventory(
    third_party_root: Path,
    package_records: list[tuple[str, str, str, tuple[str, ...]]],
    python_license: Path | None,
) -> None:
    lines = [
        "第三方授權檔案清單（建置時實際讀取的 metadata）",
        "本清單只反映可取得的套件 metadata 與安全收集到的檔案，不宣稱法律完整性。",
        "未找到授權檔案的套件會明確標示；未安裝的選配套件不列入。",
        "",
    ]
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    python_file = python_license.name if python_license is not None else "未找到可安全收集的授權檔"
    lines.append(
        f"- Python runtime | {python_version} | license metadata=未由 distribution metadata 提供 "
        f"| file={('Python/' + python_file) if python_license is not None else python_file}"
    )
    for name, version, metadata_license, files in package_records:
        file_text = ", ".join(files) if files else "未找到可安全收集的授權檔"
        lines.append(
            f"- {name} | {version} | {metadata_license} | files={file_text}"
        )
    notice = _ensure_output_file(third_party_root, "NOTICE.txt")
    with notice.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def _write_delivery_documents(root: Path, bundle: Path) -> None:
    """Add release documents and bounded third-party notices without touching runtime files."""

    root = Path(root)
    bundle = Path(bundle)
    if not root.is_dir() or _has_reparse_component(root, root):
        raise RuntimeError(f"Invalid project root: {root}")
    if not bundle.is_dir() or _has_reparse_component(bundle, bundle):
        raise RuntimeError(f"Invalid delivery bundle: {bundle}")

    project_license = root / "LICENSE"
    if not _is_safe_regular_file(project_license, max_bytes=None):
        raise RuntimeError(f"Required project LICENSE is missing or unsafe: {project_license}")
    project_license_target = _ensure_output_file(bundle, "LICENSE")
    project_license_target.write_bytes(project_license.read_bytes())

    readme_target = _ensure_output_file(bundle, "README.txt")
    with readme_target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(_DELIVERY_README)

    third_party_root = _ensure_output_directory(bundle, ("THIRD_PARTY_LICENSES",))
    python_license: Path | None = None
    for candidate_name in ("LICENSE.txt", "LICENSE"):
        candidate = Path(sys.base_prefix) / candidate_name
        if _is_safe_regular_file(candidate, max_bytes=_MAX_LICENSE_FILE_BYTES):
            python_license = candidate
            break
    copied_python_license = None
    if python_license is not None:
        python_directory = _ensure_output_directory(third_party_root, ("Python",))
        copied_python_license = _copy_bounded_license(
            python_license, python_directory, python_license.name
        )

    package_records: list[tuple[str, str, str, tuple[str, ...]]] = []
    for distribution_name in _RUNTIME_DISTRIBUTIONS:
        record = _collect_distribution_licenses(distribution_name, third_party_root)
        if record is not None:
            package_records.append(record)
    _write_inventory(third_party_root, package_records, copied_python_license)


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
    project_license = root / "LICENSE"
    for required in (
        entry,
        static_dir,
        examples_dir,
        broker_base,
        broker_paper,
        project_license,
    ):
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

    _write_delivery_documents(root, bundle)

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
