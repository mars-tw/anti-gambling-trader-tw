"""Unit tests for UI mode selection, readiness, and cleanup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import core.ui.desktop as desktop_module
import core.ui.launcher as launcher


class FakeServer:
    def __init__(self, mode="browser"):
        self.mode = mode
        self.url = "http://127.0.0.1:43210"
        self.port = 43210
        self.host_header = "127.0.0.1:43210"
        self.instance_id = "a" * 32
        self.token = "SECRET-MUST-NOT-BE-WRITTEN"
        self.shutdown_calls = 0
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        return True

    def shutdown(self):
        self.shutdown_calls += 1


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args):
        return [handler(*args) for handler in self.handlers]


class FakeWindow:
    def __init__(self):
        self.events = type(
            "FakeEvents",
            (),
            {
                "initialized": FakeEvent(),
                "loaded": FakeEvent(),
                "closed": FakeEvent(),
            },
        )()
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class FakeWebView:
    def __init__(self, renderer: str):
        self.actual_renderer = renderer
        self.settings = {}
        self.window = FakeWindow()
        self.start_kwargs = None
        self.initialized_results = []

    def create_window(self, *args, **kwargs):
        return self.window

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        self.initialized_results = self.window.events.initialized.emit(self.actual_renderer)
        if any(result is False for result in self.initialized_results):
            return
        self.window.events.loaded.emit()
        self.window.events.closed.emit()


class DesktopServer(FakeServer):
    def __init__(self):
        super().__init__("desktop")
        self._stopped = threading.Event()

    def wait(self, timeout=None):
        self.wait_calls += 1
        if timeout is None:
            return self._stopped.wait()
        return self._stopped.wait(timeout)

    def shutdown(self):
        self.shutdown_calls += 1
        self._stopped.set()


def test_parser_defaults_and_modes():
    args = launcher.build_parser().parse_args([])
    assert args.mode == "choose"
    assert args.port == 0
    assert args.no_open is False
    args = launcher.build_parser().parse_args(
        ["--mode", "desktop", "--port", "4567", "--no-open", "--ready-file", "ready.json"]
    )
    assert args.mode == "desktop"
    assert args.port == 4567
    assert args.ready_file == "ready.json"


def test_ready_file_is_exclusive_nonsecret(tmp_path: Path):
    server = FakeServer()
    target = tmp_path / "ready.json"
    launcher._write_ready_file(str(target), server)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "url": server.url,
        "pid": payload["pid"],
        "mode": "browser",
        "instance_id": server.instance_id,
        "ready": True,
    }
    assert server.token not in target.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        launcher._write_ready_file(str(target), server)


def test_browser_no_open_prints_url_and_cleans(monkeypatch, capsys):
    server = FakeServer("browser")
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: server)
    monkeypatch.setattr(launcher, "_verify_health", lambda current: {"ok": True})
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda *args, **kwargs: opened.append(args))
    assert launcher.run_ui(mode="browser", no_open=True) == 0
    assert opened == []
    assert server.shutdown_calls >= 1
    assert server.url in capsys.readouterr().out
    assert server.token not in capsys.readouterr().out


def test_browser_ready_written_only_after_health(monkeypatch, tmp_path: Path):
    server = FakeServer("browser")
    target = tmp_path / "ready.json"
    order = []
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: server)

    def healthy(current):
        assert not target.exists()
        order.append("health")
        return {"ok": True}

    monkeypatch.setattr(launcher, "_verify_health", healthy)
    assert launcher.run_ui(mode="browser", no_open=True, ready_file=str(target)) == 0
    assert order == ["health"]
    assert json.loads(target.read_text(encoding="utf-8"))["ready"] is True


def test_desktop_ready_waits_for_loaded_event(monkeypatch, tmp_path: Path):
    server = FakeServer("desktop")
    target = tmp_path / "desktop-ready.json"
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: server)
    monkeypatch.setattr(launcher, "_verify_health", lambda current: {"ok": True})

    def fake_desktop(current, *, on_loaded):
        assert current is server
        assert not target.exists()
        on_loaded("edgechromium")
        assert target.exists()

    monkeypatch.setattr(desktop_module, "run_desktop", fake_desktop)
    assert launcher.run_ui(mode="desktop", ready_file=str(target)) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["renderer"] == "edgechromium"
    assert payload["mode"] == "desktop"
    assert server.token not in target.read_text(encoding="utf-8")


def test_desktop_failure_cleans_owned_server(monkeypatch, capsys):
    server = FakeServer("desktop")
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: server)
    monkeypatch.setattr(launcher, "_verify_health", lambda current: {"ok": True})

    def unavailable(*args, **kwargs):
        raise desktop_module.DesktopUnavailable(
            "請執行 pip install '.[desktop]'，或改用 --mode browser"
        )

    monkeypatch.setattr(desktop_module, "run_desktop", unavailable)
    assert launcher.run_ui(mode="desktop") == 2
    assert server.shutdown_calls >= 1
    assert "--mode browser" in capsys.readouterr().err


def test_desktop_dependency_is_lazy_and_has_install_hint(monkeypatch):
    def missing(name):
        assert name == "webview"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(desktop_module.importlib, "import_module", missing)
    with pytest.raises(desktop_module.DesktopUnavailable) as exc:
        desktop_module._load_webview()
    message = str(exc.value)
    assert "pip install '.[desktop]'" in message
    assert "--mode browser" in message


@pytest.mark.parametrize("actual_renderer", ["edgechromium", "mshtml"])
def test_desktop_uses_initialized_renderer_and_rejects_mshtml(
    monkeypatch, tmp_path: Path, actual_renderer: str
):
    server = DesktopServer()
    webview = FakeWebView(actual_renderer)
    target = tmp_path / "desktop-ready.json"
    loaded = []
    monkeypatch.setattr(desktop_module, "_load_webview", lambda: webview)
    monkeypatch.setattr(desktop_module.sys, "platform", "win32")

    def on_loaded(renderer):
        loaded.append(renderer)
        launcher._write_ready_file(str(target), server, renderer=renderer)

    if actual_renderer == "mshtml":
        with pytest.raises(desktop_module.DesktopUnavailable) as exc:
            desktop_module.run_desktop(server, on_loaded=on_loaded)
        assert "WebView2" in str(exc.value)
        assert "--mode browser" in str(exc.value)
        assert webview.initialized_results == [False]
        assert loaded == []
        assert not target.exists()
    else:
        desktop_module.run_desktop(server, on_loaded=on_loaded)
        assert webview.initialized_results == [None]
        assert loaded == ["edgechromium"]
        assert json.loads(target.read_text(encoding="utf-8"))["renderer"] == "edgechromium"

    assert webview.settings == {"ALLOW_DOWNLOADS": True, "ALLOW_FILE_URLS": False}
    assert webview.start_kwargs == {"private_mode": True, "gui": "edgechromium"}


def test_choose_result_used_without_second_selector(monkeypatch):
    server = FakeServer("browser")
    monkeypatch.setattr(launcher, "_choose_mode", lambda: "browser")
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: server)
    monkeypatch.setattr(launcher, "_verify_health", lambda current: {"ok": True})
    assert launcher.run_ui(mode="choose", no_open=True) == 0


def test_choose_cancel_exits_without_starting_server(monkeypatch, capsys):
    started = []
    monkeypatch.setattr(launcher, "_choose_mode", lambda: None)
    monkeypatch.setattr(launcher, "start_server", lambda **kwargs: started.append(kwargs))

    assert launcher.run_ui(mode="choose") == 0
    assert started == []
    assert "已取消啟動新手工作台" in capsys.readouterr().out


def test_safe_print_tolerates_frozen_no_console(monkeypatch):
    monkeypatch.setattr(launcher.sys, "stdout", None)
    monkeypatch.setattr(launcher.sys, "stderr", None)
    launcher._safe_print("not visible")
    launcher._safe_print("not visible", error=True)


def test_configure_utf8_streams_tolerates_minimal_and_closed_streams(monkeypatch):
    class Stream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    class ClosedStream:
        def reconfigure(self, **kwargs):
            raise ValueError("I/O operation on closed file")

    stdout = Stream()
    monkeypatch.setattr(launcher.sys, "stdout", stdout)
    monkeypatch.setattr(launcher.sys, "stderr", ClosedStream())
    launcher._configure_utf8_streams()
    assert stdout.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]

    monkeypatch.setattr(launcher.sys, "stdout", object())
    monkeypatch.setattr(launcher.sys, "stderr", None)
    launcher._configure_utf8_streams()


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp950"])
def test_help_is_utf8_under_legacy_stdio(encoding):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONIOENCODING"] = encoding
    result = subprocess.run(
        [sys.executable, "-m", "core.ui", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")
    assert result.returncode == 0, stderr
    assert "反詐投資王" in stdout


def test_force_utf8_tolerates_no_stdin(monkeypatch):
    import core.cli as cli

    monkeypatch.setattr(cli.sys, "stdin", None)
    cli._force_utf8_stdout()
