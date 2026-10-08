"""Command-line launcher for browser and optional desktop UI modes."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from core.ui.server import UIServer, start_server


def _safe_print(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (AttributeError, OSError, ValueError):
        pass


def _configure_utf8_streams() -> None:
    """Make CLI diagnostics and argparse help UTF-8 when streams permit it."""

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, TypeError, ValueError):
            # Frozen/headless launchers may expose closed or minimal streams.
            continue


def _report_startup_error(message: str) -> None:
    _safe_print(message, error=True)
    if sys.stderr is not None and sys.stdout is not None:
        return
    try:
        from tkinter import messagebox

        messagebox.showerror("反詐投資王｜無法啟動", message)
    except Exception:
        pass


def _port_value(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port 必須是整數") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port 必須介於 0 與 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anti-gambling-trader ui",
        description="啟動反詐投資王新手工作台（資料只在本機工作階段處理）",
    )
    parser.add_argument(
        "--mode",
        choices=("choose", "browser", "desktop"),
        default="choose",
        help="choose 顯示原生選擇器；browser 用預設瀏覽器；desktop 用原生 WebView",
    )
    parser.add_argument("--port", type=_port_value, default=0, help="本機連接埠；0 為自動選擇")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="瀏覽器模式不自動開頁，只印出不含密鑰的本機網址",
    )
    parser.add_argument(
        "--ready-file",
        metavar="PATH",
        help="就緒後以 exclusive-create 寫入非機密 JSON（供自動驗收）",
    )
    return parser


def _choose_mode() -> str | None:
    """Show a tiny native chooser; degrade safely on headless hosts."""

    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("反詐投資王｜選擇開啟方式")
        root.resizable(False, False)
        selected: list[str | None] = []

        frame = ttk.Frame(root, padding=22)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text="要用哪一種本機介面？", font=("", 13, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 8)
        )
        ttk.Label(
            frame,
            text="兩種模式使用完全相同的本機服務與畫面。",
        ).grid(row=1, column=0, columnspan=2, pady=(0, 16))

        def pick(mode: str | None) -> None:
            selected.append(mode)
            root.destroy()

        browser = ttk.Button(
            frame,
            text="本機瀏覽器版\n（不需額外套件）",
            command=lambda: pick("browser"),
            width=24,
        )
        desktop = ttk.Button(
            frame,
            text=(
                "桌面視窗版\n（獨立應用程式視窗）"
                if _is_frozen_app()
                else "桌面視窗版\n（需要 desktop 選配）"
            ),
            command=lambda: pick("desktop"),
            width=24,
        )
        browser.grid(row=2, column=0, padx=(0, 6), ipady=8)
        desktop.grid(row=2, column=1, padx=(6, 0), ipady=8)
        root.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        root.update_idletasks()
        width = root.winfo_reqwidth()
        height = root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"+{x}+{y}")
        browser.focus_set()
        root.mainloop()
        return selected[0] if selected else None
    except Exception:
        _safe_print(
            "無法顯示原生選擇器；將使用本機瀏覽器版。"
            "也可明確執行 --mode browser，或安裝 desktop 選配後執行 --mode desktop。",
            error=True,
        )
        return "browser"


def _is_frozen_app() -> bool:
    """Whether this launcher is running as a windowed frozen application."""

    return bool(getattr(sys, "frozen", False))


def _run_browser_status_controller(server: UIServer) -> None:
    """Keep a visible owner for a choose/frozen browser session.

    Browser tabs can be closed independently of the local session.  This small
    native controller stays visible with an explicit reopen/end choice, so a
    windowed launcher never leaves an invisible listener behind.
    """

    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
    except Exception:
        _safe_print("無法顯示工作階段控制視窗；請在終端機按 Ctrl+C 結束本次使用。", error=True)
        server.wait()
        return

    root.title("反詐投資王｜本機工作階段")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=22)
    frame.grid(row=0, column=0, sticky="nsew")
    ttk.Label(frame, text="本機工作階段仍在使用中", font=("", 13, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    status = tk.StringVar(value="可重新開啟工作台；結束前請先下載要保留的資料。")
    ttk.Label(frame, textvariable=status, wraplength=360, justify="left").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(7, 14)
    )

    warning = ttk.Label(
        frame,
        text="正常結束會清理本機暫存；非預期失敗或強制停止可能留下系統暫存。需要保留請先下載，然後送出結束要求。",
        wraplength=360,
        justify="left",
    )
    cancel_end = ttk.Button(frame, text="返回繼續使用")
    confirm_end = ttk.Button(frame, text="送出結束要求")
    warning_visible = False

    def reopen() -> None:
        try:
            opened = webbrowser.open(server.url, new=1, autoraise=True)
        except Exception:
            opened = False
        status.set("已要求重新開啟工作台。" if opened else "無法自動開啟；可從既有瀏覽器視窗再試。")

    def hide_end_warning() -> None:
        nonlocal warning_visible
        if not warning_visible:
            return
        warning.grid_remove()
        cancel_end.grid_remove()
        confirm_end.grid_remove()
        warning_visible = False
        status.set("可重新開啟工作台；結束前請先下載要保留的資料。")

    def show_end_warning() -> None:
        nonlocal warning_visible
        if warning_visible:
            confirm_end.focus_set()
            return
        warning_visible = True
        warning.grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 8))
        cancel_end.grid(row=4, column=0, sticky="ew", padx=(0, 5))
        confirm_end.grid(row=4, column=1, sticky="ew", padx=(5, 0))
        status.set("請選擇：返回繼續使用，或送出結束要求。")
        confirm_end.focus_set()

    def end_session() -> None:
        confirm_end.state(["disabled"])
        cancel_end.state(["disabled"])
        status.set("正在送出結束要求…")
        root.update_idletasks()
        try:
            server.shutdown()
        except Exception:
            status.set("暫時無法結束本次使用；請稍後再試。")
            confirm_end.state(["!disabled"])
            cancel_end.state(["!disabled"])
            return
        root.destroy()

    reopen_button = ttk.Button(frame, text="重新開啟工作台", command=reopen)
    end_button = ttk.Button(frame, text="結束本次使用", command=show_end_warning)
    reopen_button.grid(row=2, column=0, sticky="ew", padx=(0, 5))
    end_button.grid(row=2, column=1, sticky="ew", padx=(5, 0))
    cancel_end.configure(command=hide_end_warning)
    confirm_end.configure(command=end_session)
    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)
    root.protocol("WM_DELETE_WINDOW", show_end_warning)

    def poll_session() -> None:
        if server.wait(0):
            try:
                root.destroy()
            except tk.TclError:
                pass
            return
        root.after(250, poll_session)

    root.after(250, poll_session)
    root.update_idletasks()
    width = root.winfo_reqwidth()
    height = root.winfo_reqheight()
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"+{x}+{y}")
    reopen_button.focus_set()
    root.mainloop()


def _verify_health(server: UIServer, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1.0)
            connection.request(
                "GET",
                "/api/health",
                headers={"Host": server.host_header, "Cache-Control": "no-store"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"本機健康檢查回應 HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
            if (
                payload.get("ok") is True
                and payload.get("service") == "anti-gambling-trader-ui"
                and payload.get("instance_id") == server.instance_id
                and payload.get("mode") == server.mode
            ):
                return payload
            last_error = RuntimeError("本機健康檢查回應不屬於這個工作階段")
        except Exception as exc:
            last_error = exc
        finally:
            if connection is not None:
                connection.close()
        time.sleep(0.05)
    raise RuntimeError("本機 UI 健康檢查逾時") from last_error


def _write_ready_file(
    path_value: str,
    server: UIServer,
    *,
    renderer: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "url": server.url,
        "pid": os.getpid(),
        "mode": server.mode,
        "instance_id": server.instance_id,
        "ready": True,
    }
    if renderer:
        payload["renderer"] = renderer
    path = Path(path_value)
    # x mode is part of the readiness contract: never overwrite a stale file.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_ui(
    *,
    mode: str = "choose",
    port: int = 0,
    no_open: bool = False,
    ready_file: str | None = None,
) -> int:
    selected = _choose_mode() if mode == "choose" else mode
    if selected is None:
        _safe_print("已取消啟動新手工作台。")
        return 0
    server: UIServer | None = None
    try:
        server = start_server(port=port, mode=selected)
        _verify_health(server)

        if selected == "browser":
            if ready_file:
                _write_ready_file(ready_file, server)
            _safe_print(f"本機新手工作台：{server.url}")
            if not no_open:
                try:
                    opened = webbrowser.open(server.url, new=1, autoraise=True)
                except Exception:
                    opened = False
                if not opened:
                    _safe_print("無法自動開啟瀏覽器；請複製上方本機網址。", error=True)
            if not no_open and (mode == "choose" or _is_frozen_app()):
                _run_browser_status_controller(server)
            else:
                # Explicit CLI/headless runs retain the terminal-owned
                # lifecycle: --no-open and Ctrl+C remain useful to automation.
                server.wait()
            return 0

        from core.ui.desktop import run_desktop

        def desktop_loaded(renderer: str | None) -> None:
            if ready_file:
                _write_ready_file(ready_file, server, renderer=renderer)

        run_desktop(server, on_loaded=desktop_loaded)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        _report_startup_error(f"無法啟動新手工作台：{exc}")
        return 2
    finally:
        if server is not None:
            server.shutdown()
            server.wait(15.0)


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_streams()
    args = build_parser().parse_args(argv)
    return run_ui(
        mode=args.mode,
        port=args.port,
        no_open=args.no_open,
        ready_file=args.ready_file,
    )


__all__ = ["build_parser", "main", "run_ui"]
