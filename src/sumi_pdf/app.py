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


def main():
    import uvicorn
    import webview

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
