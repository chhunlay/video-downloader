"""
Desktop shell for the Video Downloader.

Runs the existing Flask app (app.py) in a background thread and opens it
inside a native window via pywebview - no browser tab, no visible
terminal, no separate "start the server" step for the end user.

This is what PyInstaller packages into a single .exe. Build it (on a
Windows machine, since PyInstaller does not cross-compile) with:

    pyinstaller --onefile --windowed --name VideoDownloader ^
        --add-data "templates;templates" ^
        desktop_app.py

The resulting dist\\VideoDownloader.exe is the whole app.
"""

import socket
import sys
import threading

import webview

import app as flask_app  # the existing app.py - reused unchanged

HOST = "127.0.0.1"


def find_free_port():
    """app.py hardcodes port 5050, but bind to an OS-assigned free port
    instead so a leftover process (or another copy of the app) can never
    collide with it - the actual port is discovered below, not guessed."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def run_flask(port):
    # debug=False and use_reloader=False are required here: the reloader
    # forks a second process expecting to be launched as a normal Python
    # script, which breaks (or double-launches the window) once this is
    # frozen into a single .exe by PyInstaller.
    flask_app.app.run(host=HOST, port=port, debug=False, use_reloader=False, threaded=True)


def main():
    port = find_free_port()

    server_thread = threading.Thread(target=run_flask, args=(port,), daemon=True)
    server_thread.start()

    webview.create_window(
        "Video Downloader",
        f"http://{HOST}:{port}/",
        width=520,
        height=780,
        resizable=True,
    )
    # blocks until the window is closed, then the process exits
    # (the Flask thread is a daemon thread, so it's torn down with it)
    webview.start()


if __name__ == "__main__":
    sys.exit(main() or 0)
