"""Optional native window adapter for the shared loopback UI."""

from __future__ import annotations

import importlib
import sys
import threading
from typing import Callable

from core.ui.server import UIServer


class DesktopUnavailable(RuntimeError):
    """Raised when the optional desktop renderer cannot be used."""


def _load_webview():
    try:
        return importlib.import_module("webview")
    except Exception as exc:
        raise DesktopUnavailable(
            "桌面視窗需要 pywebview。請執行 pip install '.[desktop]'，"
            "或改用 --mode browser。"
        ) from exc


def run_desktop(
    server: UIServer,
    *,
    on_loaded: Callable[[str | None], None] | None = None,
) -> None:
    """Show the exact same HTTP UI in a real native WebView window.

    ``webview.start`` is deliberately called here on the caller's main thread.
    No Python bridge is attached to the page; all operations use the protected
    loopback HTTP API.
    """

    if threading.current_thread() is not threading.main_thread():
        raise DesktopUnavailable("桌面視窗必須由主執行緒啟動；請改從 UI 啟動器執行。")

    webview = _load_webview()
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
    except Exception as exc:
        raise DesktopUnavailable(
            "目前 pywebview 版本不支援必要的安全設定；"
            "請安裝 pywebview>=6.2.1,<7，或改用 --mode browser。"
        ) from exc

    try:
        window = webview.create_window(
            "反詐投資王｜新手工作台",
            server.url,
            width=1240,
            height=820,
            min_size=(360, 600),
            resizable=True,
            text_select=True,
        )
    except Exception as exc:
        raise DesktopUnavailable(
            "無法建立桌面視窗。Windows 請確認已安裝 Microsoft Edge WebView2；"
            "也可以改用 --mode browser。"
        ) from exc

    ready_once = threading.Event()
    native_closed = threading.Event()
    initialized_seen = threading.Event()
    renderer_rejected = threading.Event()
    callback_errors: list[BaseException] = []
    renderer_error: DesktopUnavailable | None = None
    observed_renderer: str | None = None

    def handle_initialized(renderer: str | None = None) -> bool | None:
        nonlocal observed_renderer, renderer_error
        observed_renderer = str(renderer) if renderer is not None else None
        initialized_seen.set()
        if sys.platform == "win32" and observed_renderer != "edgechromium":
            renderer_rejected.set()
            renderer_error = DesktopUnavailable(
                "Windows 桌面視窗實際使用的渲染器不是 Edge Chromium；"
                "請安裝或修復 Microsoft Edge WebView2，或改用 --mode browser。"
            )
            return False
        return None

    def handle_loaded() -> None:
        nonlocal renderer_error
        if ready_once.is_set() or renderer_rejected.is_set():
            return
        if sys.platform == "win32" and not initialized_seen.is_set():
            renderer_rejected.set()
            renderer_error = DesktopUnavailable(
                "Windows 桌面視窗未回報實際渲染器；"
                "請安裝或修復 Microsoft Edge WebView2，或改用 --mode browser。"
            )
            return
        ready_once.set()
        try:
            if on_loaded is not None:
                on_loaded(observed_renderer)
        except BaseException as exc:  # propagate after the GUI loop exits
            callback_errors.append(exc)
            threading.Thread(target=server.shutdown, daemon=True).start()

    def handle_closed() -> None:
        native_closed.set()
        threading.Thread(
            target=server.shutdown,
            name=f"ui-native-stop-{server.instance_id[:8]}",
            daemon=True,
        ).start()

    window.events.initialized += handle_initialized
    window.events.loaded += handle_loaded
    window.events.closed += handle_closed

    def close_window_after_http_shutdown() -> None:
        server.wait()
        if native_closed.is_set():
            return
        try:
            window.destroy()
        except Exception:
            pass

    monitor = threading.Thread(
        target=close_window_after_http_shutdown,
        name=f"ui-native-watch-{server.instance_id[:8]}",
        daemon=True,
    )
    monitor.start()

    try:
        kwargs = {"private_mode": True}
        if sys.platform == "win32":
            # Explicit Edge Chromium only.  Never fall back to legacy mshtml.
            kwargs["gui"] = "edgechromium"
        webview.start(**kwargs)
    except Exception as exc:
        if renderer_error is not None:
            raise renderer_error from exc
        if callback_errors:
            raise callback_errors[0]
        raise DesktopUnavailable(
            "桌面視窗啟動失敗。Windows 請安裝或修復 Microsoft Edge WebView2；"
            "也可以使用 --mode browser。"
        ) from exc
    finally:
        server.shutdown()
        server.wait(15.0)

    if renderer_error is not None:
        raise renderer_error
    if callback_errors:
        raise callback_errors[0]
    if not ready_once.is_set():
        raise DesktopUnavailable("桌面視窗在介面載入前已關閉；尚未宣告就緒。")


__all__ = ["DesktopUnavailable", "run_desktop"]
