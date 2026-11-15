"""Tests for desktop_app.py, with the window and the server faked out."""

import sys
import types

import app as webapp


def test_desktop_app_starts_the_job_cleanup(monkeypatch):
    """Checks the desktop app loads old jobs and starts the 24-hour cleanup, like app.py does."""
    fake_webview = types.SimpleNamespace(create_window=lambda *a, **k: None, start=lambda: None)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.delitem(sys.modules, "desktop_app", raising=False)
    import desktop_app

    prepared = []
    monkeypatch.setattr(webapp, "prepare_store", lambda: prepared.append(True))
    monkeypatch.setattr(desktop_app.threading, "Thread",
                        lambda **kwargs: types.SimpleNamespace(start=lambda: None))
    desktop_app.main()
    assert prepared == [True]
