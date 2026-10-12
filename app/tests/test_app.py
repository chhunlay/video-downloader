"""Tests for the Flask routes in app.py, with downloads faked out."""

import json

import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    Gives a Flask test client that serves files from a temp folder.

    Parameters:
    tmp_path (pathlib.Path): pytest's temp folder.
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.

    Returns:
    flask.testing.FlaskClient: The test client.
    """
    monkeypatch.setattr(webapp, "DOWNLOAD_DIR", str(tmp_path))
    webapp.progress_data.update(percent=0, status="idle", filename="")
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def test_index_page(client):
    """Checks the main page renders."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Video Downloader" in response.data


def test_info_returns_preview(client, monkeypatch):
    """Checks /info returns the preview fields and resolutions."""
    fake_info = {
        "title": "Clip", "thumbnail": "t.jpg", "uploader": "Me", "duration": 10,
        "formats": [{"height": 720, "vcodec": "avc1", "filesize": 1024 * 1024}],
    }
    monkeypatch.setattr(webapp, "get_video_info", lambda url: fake_info)
    data = client.post("/info", data={"url": "https://x"}).get_json()
    assert data["title"] == "Clip"
    assert data["resolutions"] == [[720, "1.0MB"]]


def test_info_reports_errors(client, monkeypatch):
    """Checks /info turns an exception into an error message."""
    def boom(url):
        raise RuntimeError("Video unavailable")
    monkeypatch.setattr(webapp, "get_video_info", boom)
    assert client.post("/info", data={"url": "bad"}).get_json() == {"error": "Video unavailable"}


def test_download_starts_background_task(client, monkeypatch):
    """Checks /download passes the form fields to the background task."""
    calls = []

    class InlineThread:
        """Runs the target right away instead of on a real thread."""

        def __init__(self, target, args):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(webapp.threading, "Thread", InlineThread)
    monkeypatch.setattr(webapp, "download_task", lambda *args: calls.append(args))
    response = client.post("/download", data={"url": "https://x", "type": "video", "resolution": "720"})
    assert response.get_json() == {"started": True}
    assert calls == [("https://x", "video", "720")]


def test_download_task_success(tmp_path, monkeypatch):
    """Checks a finished download reports "done" and the file name."""
    monkeypatch.setattr(webapp, "download_media", lambda *a, **k: str(tmp_path / "clip.mp4"))
    webapp.download_task("https://x", "video")
    assert webapp.progress_data["status"] == "done"
    assert webapp.progress_data["filename"] == "clip.mp4"


def test_download_task_failure(monkeypatch):
    """Checks a failed download reports the error."""
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(webapp, "download_media", boom)
    webapp.download_task("https://x", "video")
    assert webapp.progress_data["status"] == "error: nope"


def test_progress_hook_states():
    """Checks yt-dlp progress events map to the page's statuses."""
    webapp.progress_hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 4})
    assert webapp.progress_data["percent"] == 25
    webapp.progress_hook({"status": "finished"})
    assert webapp.progress_data["status"] == "processing"
    webapp.progress_hook({"status": "normalizing", "percent": 60})
    assert webapp.progress_data["status"] == "optimizing for social media"
    assert webapp.progress_data["percent"] == 60
    webapp.progress_hook({"status": "error"})
    assert webapp.progress_data["status"] == "error"


def test_progress_stream_stops_when_done(client):
    """Checks the progress stream sends the final state and then ends."""
    webapp.progress_data.update(percent=100, status="done", filename="clip.mp4")
    body = client.get("/progress").get_data(as_text=True)
    events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    assert events == [{"percent": 100, "status": "done", "filename": "clip.mp4"}]


def test_download_file_serves_then_deletes(client, tmp_path):
    """Checks a finished file is sent once and then removed from disk."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video-bytes")
    response = client.get("/downloads/clip.mp4")
    assert response.data == b"video-bytes"
    assert "attachment" in response.headers["Content-Disposition"]
    response.close()
    assert not path.exists()


def test_download_file_blocks_path_traversal(client):
    """Checks a ../ path can't read files outside the downloads folder."""
    assert client.get("/downloads/../app.py").status_code == 404
