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


def main():
    import uvicorn
    import webview

    from .server import app

    port = _free_port()
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True).start()
    time.sleep(0.3)
    webview.create_window("Sumi PDF", f"http://127.0.0.1:{port}/",
                          width=1280, height=860, min_size=(960, 640))
    webview.start()


if __name__ == "__main__":
    main()
