"""
Desktop shell: runs app.py's Flask server in a thread and shows it in a native pywebview window.

This is what PyInstaller packages into VideoDownloader.exe (build on Windows,
see build_windows.bat and BUILD_WINDOWS.md).
"""

import socket
import sys
import threading

import webview

import app as flask_app

HOST = "127.0.0.1"


def find_free_port():
    """
    Finds a free port, so a leftover process can't collide with ours.

    Returns:
    int: A port number chosen by the OS.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def run_flask(port):
    """
    Runs the Flask server (blocks).

    Parameters:
    port (int): Port to listen on.
    """
    # The debug reloader forks a second process, which breaks inside a frozen .exe
    flask_app.app.run(host=HOST, port=port, debug=False, use_reloader=False, threaded=True)


def main():
    """Starts the server and opens the app window; returns when the window closes."""
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
    # Blocks until the window closes; the daemon server thread exits with it
    webview.start()


if __name__ == "__main__":
    sys.exit(main() or 0)
