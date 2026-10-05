"""Windows desktop shell: WebView2 window over the local server.

  pip install -e .[app]
  sumi-app            (or: python -m sumi_pdf.app)
"""
from __future__ import annotations

import os
import socket
import threading
import time


def _free_port(default: int = 8765) -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", default))
        return default
    except OSError:
        s.close()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        try:
            s.close()
        except OSError:
            pass


def _first_path(result):
    """Normalise a pywebview dialog result (tuple of paths, or None)."""
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result)


def _native_dialog(window, dialog_type, **kwargs):
    """Pop the native file dialog, marshalled onto the WinForms UI thread.

    ``Window.create_file_dialog`` ends in ``WinForms.OpenFileDialog.ShowDialog``.
    WinForms only allows a dialog to be shown on the thread that owns the owner
    window, but FastAPI/uvicorn handlers run on worker threads (pywebview's own
    js_api dispatch also uses a fresh thread). So we marshal with the same
    ``Control.Invoke`` pattern pywebview uses internally, falling back to a
    direct call if the WinForms backend is not active.
    """
    def _do():
        return window.create_file_dialog(dialog_type, **kwargs)

    try:
        from System import Func, Type
        from webview.platforms import winforms

        form = winforms.BrowserView.instances.get(window.uid)
        if form is not None and form.InvokeRequired:
            box: dict = {}

            def _delegate():
                box["v"] = _do()

            form.Invoke(Func[Type](_delegate))
            return box.get("v")
    except Exception:
        pass

    return _do()


def _check_prerequisites() -> str | None:
    """Return a human-readable reason if the GUI prerequisites are missing.

    The frozen app needs (1) .NET Framework 4.7.2+ for pythonnet/clr (pywebview
    WinForms backend) and (2) the WebView2 runtime for the browser control.
    Both ship with Windows 10 1809+ / 11 by default, but stripped-down builds
    (LTSC, VMs, Server without Desktop Experience) may lack them — and the raw
    ``Failed to resolve Python.Runtime.Loader.Initialize`` traceback gives no
    hint. Fail early with an actionable message instead.
    """
    if os.name != "nt":
        return None
    msgs = []
    try:
        import _winreg as winreg  # type: ignore[import-not-found]
    except ImportError:  # PyInstaller bundles _winreg as winreg
        import winreg  # type: ignore[no-redef]

    # .NET Framework 4.x full install is registered under this key.
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            release = winreg.QueryValueEx(k, "Release")[0]
            if release < 461808:  # 4.7.2
                msgs.append(
                    ".NET Framework 4.7.2以上が見つかりません（Release=%d）。"
                    "「.NET Framework 4.8 ランタイム」をインストールしてください。" % release)
    except OSError:
        msgs.append(
            ".NET Framework 4.x が見つかりません。"
            "「.NET Framework 4.8 ランタイム」をインストールしてください。")

    # WebView2 runtime (Evergreen) — pywebview needs it for the browser control.
    for sub in (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub):
                break
        except OSError:
            continue
    else:
        msgs.append(
            "Microsoft Edge WebView2 ランタイムが見つかりません。"
            "「WebView2 Runtime (Evergreen)」をインストールしてください。")

    return "\n\n".join(msgs) if msgs else None


def _fatal_dialog(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "SUMIPDF 起動エラー", 0x10)
    except Exception:
        print(text)


def main():
    import uvicorn
    import webview

    missing = _check_prerequisites()
    if missing:
        _fatal_dialog(missing + "\n\nインストール後に再度 SUMIPDF.exe を起動してください。")
        return

    from . import server as sv
    from .server import app

    port = _free_port()
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True).start()
    time.sleep(0.3)
    window = webview.create_window("SUMIPDF", f"http://127.0.0.1:{port}/",
                                   width=1280, height=860, min_size=(960, 640))

    def open_dialog():
        result = _native_dialog(
            window, webview.FileDialog.OPEN,
            file_types=("PDF (*.pdf)",))
        return _first_path(result)

    def save_dialog(suggested=None):
        result = _native_dialog(
            window, webview.FileDialog.SAVE,
            save_filename=suggested or "",
            file_types=("PDF (*.pdf)",))
        return _first_path(result)

    sv.register_dialogs(open_fn=open_dialog, save_fn=save_dialog)
    webview.start()


if __name__ == "__main__":
    main()
