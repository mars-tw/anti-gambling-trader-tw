"""Deep-audit regressions for CLI output safety and scaffold confinement."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.cli import main
from core.scaffold.generator import GeneratedFile, ScaffoldOptions, write_project


ROOT = Path(__file__).resolve().parents[1]


def _write_source(path: Path) -> bytes:
    content = (
        "symbol,exit_time,pnl,pnl_currency,tag\n"
        "AAPL,2024-01-01,10,USD,test\n"
        "AAPL,2024-01-02,-2,USD,test\n"
        "AAPL,2024-01-03,12,USD,test\n"
    ).encode("utf-8")
    path.write_bytes(content)
    return content


@pytest.mark.parametrize("flag", ["--json", "--html", "--strategy", "--card"])
def test_analyze_rejects_each_output_aliasing_input_without_data_loss(
    tmp_path, flag, capsys
):
    source = tmp_path / "trades.csv"
    original = _write_source(source)

    assert main(["analyze", str(source), flag, str(source)]) == 1
    assert source.read_bytes() == original
    assert "拒絕覆蓋輸入資料" in capsys.readouterr().err


def test_analyze_rejects_hardlink_alias_without_data_loss(tmp_path, capsys):
    source = tmp_path / "trades.csv"
    original = _write_source(source)
    hardlink = tmp_path / "hardlink.json"
    try:
        os.link(source, hardlink)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    assert main(["analyze", str(source), "--json", str(hardlink)]) == 1
    assert source.read_bytes() == original
    assert hardlink.read_bytes() == original
    assert "拒絕覆蓋輸入資料" in capsys.readouterr().err


def test_analyze_rejects_output_output_alias_before_any_write(tmp_path, capsys):
    source = tmp_path / "trades.csv"
    _write_source(source)
    shared = tmp_path / "shared.out"

    assert main([
        "analyze", str(source),
        "--json", str(shared),
        "--html", str(tmp_path / "." / "shared.out"),
    ]) == 1
    assert not shared.exists()
    assert "輸出路徑衝突" in capsys.readouterr().err


def test_preflight_failure_leaves_other_requested_outputs_absent(tmp_path, capsys):
    source = tmp_path / "trades.csv"
    original = _write_source(source)
    first = tmp_path / "would-have-been.json"

    assert main([
        "analyze", str(source),
        "--json", str(first),
        "--html", str(source),
    ]) == 1
    assert not first.exists()
    assert source.read_bytes() == original
    assert "拒絕覆蓋輸入資料" in capsys.readouterr().err


def test_existing_report_destination_is_not_overwritten(tmp_path, capsys):
    source = tmp_path / "trades.csv"
    _write_source(source)
    output = tmp_path / "report.json"
    output.write_text("sentinel", encoding="utf-8")

    assert main(["analyze", str(source), "--json", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == "sentinel"
    assert "輸出檔已存在" in capsys.readouterr().err


@pytest.mark.parametrize("source_mode", ["image", "text-file"])
def test_scan_screenshot_rejects_json_aliasing_file_source(
    tmp_path, source_mode, capsys
):
    source = tmp_path / ("capture.png" if source_mode == "image" else "ocr.txt")
    original = b"not parsed because preflight must run first"
    source.write_bytes(original)
    argv = ["scan-screenshot"]
    if source_mode == "image":
        argv.append(str(source))
    else:
        argv.extend(["--text-file", str(source)])
    argv.extend(["--json", str(source)])

    assert main(argv) == 1
    assert source.read_bytes() == original
    assert "拒絕覆蓋輸入資料" in capsys.readouterr().err


def test_destination_io_error_is_clean_cli_failure(tmp_path, capsys):
    source = tmp_path / "trades.csv"
    _write_source(source)
    missing_parent_output = tmp_path / "missing" / "report.json"

    assert main([
        "analyze", str(source), "--json", str(missing_parent_output)
    ]) == 1
    captured = capsys.readouterr()
    assert "無法寫入 --json" in captured.err
    assert "Traceback" not in captured.err


def test_repeated_scaffold_preserves_custom_strategy(tmp_path):
    opts = ScaffoldOptions(project_name="safe_bot", broker="paper")
    root = write_project(opts, tmp_path)
    strategy = root / "strategy.py"
    strategy.write_text("# user customization\n", encoding="utf-8")

    with pytest.raises(ValueError, match="已存在且非空"):
        write_project(ScaffoldOptions(project_name="safe_bot", broker="paper"), tmp_path)
    assert strategy.read_text(encoding="utf-8") == "# user customization\n"


def test_scaffold_exclusive_creation_preserves_concurrent_strategy(
    tmp_path, monkeypatch
):
    import core.scaffold.generator as generator

    root = tmp_path / "racing_bot"
    monkeypatch.setattr(
        generator,
        "generate_project",
        lambda _opts: [GeneratedFile("strategy.py", "generated")],
    )
    original_mkdir = generator.Path.mkdir
    injected = False

    def mkdir(path, *args, **kwargs):  # noqa: ANN001
        nonlocal injected
        result = original_mkdir(path, *args, **kwargs)
        if path == root and not injected:
            injected = True
            (path / "strategy.py").write_text("sentinel", encoding="utf-8")
            (path / "unrelated.txt").write_text("keep", encoding="utf-8")
        return result

    monkeypatch.setattr(generator.Path, "mkdir", mkdir)
    with pytest.raises(FileExistsError):
        write_project(ScaffoldOptions(project_name="racing_bot"), tmp_path)

    assert (root / "strategy.py").read_text(encoding="utf-8") == "sentinel"
    assert (root / "unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_scaffold_validates_trimmed_name_before_building_root(tmp_path):
    opts = ScaffoldOptions(project_name="  trimmed_bot  ", broker="paper")
    root = write_project(opts, tmp_path)
    assert root == tmp_path / "trimmed_bot"
    assert root.is_dir()
    assert not (tmp_path / "  trimmed_bot  ").exists()


def test_scaffold_rejects_generated_path_traversal_without_partial_files(
    tmp_path, monkeypatch
):
    import core.scaffold.generator as generator

    monkeypatch.setattr(
        generator,
        "generate_project",
        lambda _opts: [
            GeneratedFile("safe.txt", "safe"),
            GeneratedFile("../escape.txt", "escape"),
        ],
    )
    with pytest.raises(ValueError, match="路徑不合法|越過"):
        write_project(ScaffoldOptions(project_name="traversal"), tmp_path)
    assert not (tmp_path / "traversal").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_scaffold_rejects_symlink_destination_when_supported(tmp_path):
    real_destination = tmp_path / "real"
    real_destination.mkdir()
    linked_destination = tmp_path / "linked"
    try:
        linked_destination.symlink_to(real_destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink/reparse"):
        write_project(ScaffoldOptions(project_name="bot"), linked_destination)
    assert list(real_destination.iterdir()) == []


def test_cli_scaffold_existing_nonempty_root_is_normal_nonzero(tmp_path, capsys):
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "strategy.py"
    sentinel.write_text("custom", encoding="utf-8")

    rc = main([
        "scaffold", "--name", "existing", "--out", str(tmp_path),
    ])
    assert rc == 1
    assert sentinel.read_text(encoding="utf-8") == "custom"
    captured = capsys.readouterr()
    assert "已存在且非空" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("io_encoding", ["ascii", "cp1252", "cp950"])
def test_generated_paper_main_forces_utf8_output_under_windows_encodings(
    tmp_path, io_encoding
):
    root = write_project(
        ScaffoldOptions(project_name="paper_encoding_bot", broker="paper"),
        tmp_path,
    )
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "0", "PYTHONIOENCODING": io_encoding})

    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")

    assert result.returncode == 0, stderr
    assert "策略執行完畢（paper 模式）" in stdout
    assert "最終權益" in stdout
    assert "成交筆數" in stdout
    assert "紙上模擬" in (root / "README.md").read_text(encoding="utf-8")
    assert (root / "chart.html").is_file()


def test_skill_copies_are_byte_identical():
    agents = ROOT / ".agents" / "skills" / "anti-gambling-trader" / "SKILL.md"
    claude = ROOT / ".claude" / "skills" / "anti-gambling-trader" / "SKILL.md"
    assert agents.read_bytes() == claude.read_bytes()


def test_ci_matrix_and_relocated_wheel_smoke_contract():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    for expected in (
        "permissions:",
        "contents: read",
        "ubuntu-latest",
        "windows-latest",
        '"3.10"',
        '"3.13"',
        "pip install -e .[dev] build",
        "python -m pytest",
        "python -m build --wheel",
        "PYTHONPATH",
        "start",
        "demo",
        "scaffold",
    ):
        assert expected in workflow
