"""Tests for the Flask routes in app.py, with downloads faked out."""

import json
import os

import pytest
import yt_dlp

import app as webapp
from jobs import JobStore


class InlineThread:
    """Runs the target right away instead of on a real thread."""

    def __init__(self, target, args):
        """
        Stores the target and its arguments.

        Parameters:
        target (callable): The function to run.
        args (tuple): Its positional arguments.
        """
        self.target, self.args = target, args

    def start(self):
        """Runs the target now."""
        self.target(*self.args)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """
    Gives the app a job store in a temp folder and runs workers synchronously.

    Parameters:
    tmp_path (pathlib.Path): pytest's temp folder.
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.

    Returns:
    JobStore: The store the app now uses.
    """
    job_store = JobStore(str(tmp_path))
    monkeypatch.setattr(webapp, "store", job_store)
    monkeypatch.setattr(webapp.threading, "Thread", InlineThread)
    return job_store


@pytest.fixture
def client(store):
    """
    Gives a Flask test client backed by the temp job store.

    Parameters:
    store (JobStore): The temp store fixture.

    Returns:
    flask.testing.FlaskClient: The test client.
    """
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def fake_download(calls=None, extra_files=(), error=None):
    """
    Builds a stand-in for download_media that writes clip.mp4 into out_dir.

    Parameters:
    calls (list/None): Receives the kwargs of each call.
    extra_files (tuple): More file names to create next to the clip.
    error (Exception/None): Raised instead of downloading.

    Returns:
    callable: The fake.
    """
    def fake(url, dtype, out_dir, **kwargs):
        if calls is not None:
            calls.append({"url": url, "dtype": dtype, **kwargs})
        if error:
            raise error
        for name in ("clip.mp4", *extra_files):
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(b"bytes-" + name.encode())
        return os.path.join(out_dir, "clip.mp4")
    return fake


def start(client, **form):
    """
    Posts a single download and returns the response.

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    **form: Form fields; url and type default to a video link.

    Returns:
    flask.Response: The /download response.
    """
    data = {"url": "https://x", "type": "video", **form}
    return client.post("/download", data=data)


def test_index_page(client):
    """Checks the main page renders."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Video Downloader" in response.data


def test_info_returns_preview(client, monkeypatch):
    """Checks /info returns the preview fields, resolutions and subtitle languages."""
    fake_info = {
        "title": "Clip", "thumbnail": "t.jpg", "uploader": "Me", "duration": 10,
        "formats": [{"height": 720, "vcodec": "avc1", "filesize": 1024 * 1024}],
        "subtitles": {"km": [{"name": "Khmer"}]},
    }
    monkeypatch.setattr(webapp, "get_video_info", lambda url: fake_info)
    data = client.post("/info", data={"url": "https://x"}).get_json()
    assert data["title"] == "Clip"
    assert data["resolutions"] == [[720, "1.0MB"]]
    assert data["subtitles"] == [{"code": "km", "name": "Khmer", "auto": False}]


def test_info_reports_errors(client, monkeypatch):
    """Checks /info turns an exception into an error message."""
    def boom(url):
        raise RuntimeError("Video unavailable")
    monkeypatch.setattr(webapp, "get_video_info", boom)
    assert client.post("/info", data={"url": "bad"}).get_json() == {"error": "Video unavailable"}


def test_download_single_success(client, store, monkeypatch):
    """Checks a single download becomes a finished job with its file."""
    monkeypatch.setattr(webapp, "download_media", fake_download())
    job_id = start(client).get_json()["job_id"]
    job = store.get(job_id)
    assert job.status == "done"
    assert job.files == ["clip.mp4"]
    assert job.title == "clip"


def test_download_passes_options(client, monkeypatch):
    """Checks form options reach download_media in the right shape."""
    calls = []
    monkeypatch.setattr(webapp, "download_media", fake_download(calls))
    start(client, audio_quality="320", trim_start="0:30", trim_end="1:15",
          sub_lang="km", sub_mode="srt", resolution="720")
    kwargs = calls[0]
    assert kwargs["trim"] == (30, 75)
    assert kwargs["audio_quality"] == "320"
    assert kwargs["subtitles"] == {"lang": "km", "mode": "srt"}
    assert kwargs["resolution"] == "720"


def test_download_defaults(client, monkeypatch):
    """Checks a bare form downloads everything at 192 kbps with no subtitles."""
    calls = []
    monkeypatch.setattr(webapp, "download_media", fake_download(calls))
    start(client, type="audio")
    kwargs = calls[0]
    assert kwargs["trim"] is None
    assert kwargs["audio_quality"] == "192"
    assert kwargs["subtitles"] is None


def test_download_includes_srt_file(client, store, monkeypatch):
    """Checks a saved .srt is listed after the video."""
    monkeypatch.setattr(webapp, "download_media", fake_download(extra_files=("clip.km.srt",)))
    job_id = start(client, sub_lang="km", sub_mode="srt").get_json()["job_id"]
    assert store.get(job_id).files == ["clip.mp4", "clip.km.srt"]


@pytest.mark.parametrize("form", [
    {"trim_start": "abc"},
    {"trim_start": "2:00", "trim_end": "1:00"},
    {"audio_quality": "999"},
    {"sub_lang": "km", "sub_mode": "xyz"},
    {"sub_mode": "srt"},
    {"type": "gif"},
    {"url": ""},
    {"resolution": "big"},
])
def test_download_rejects_bad_input(client, tmp_path, form):
    """
    Checks invalid forms get a 400 with a message and create no job.

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    tmp_path (pathlib.Path): The store's folder.
    form (dict): The bad fields.
    """
    response = start(client, **form)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert os.listdir(tmp_path) == []


def test_download_failure_sets_error(client, store, monkeypatch):
    """Checks a failed download stores a short, readable error."""
    error = yt_dlp.utils.DownloadError("ERROR: [youtube] abc123: Video unavailable")
    monkeypatch.setattr(webapp, "download_media", fake_download(error=error))
    job_id = start(client).get_json()["job_id"]
    job = store.get(job_id)
    assert job.status == "error"
    assert job.error == "Video unavailable"


def read_events(response):
    """
    Parses a server-sent events body.

    Parameters:
    response (flask.Response): The /progress response.

    Returns:
    list: The decoded event payloads.
    """
    body = response.get_data(as_text=True)
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def test_progress_stream_for_job(client, store):
    """Checks a finished job streams one event with its files, then ends."""
    job = store.create("single", "video", {})
    store.update(job.id, status="done", percent=100, files=["clip.mp4"])
    events = read_events(client.get(f"/progress/{job.id}"))
    assert len(events) == 1
    assert events[0]["files"] == ["clip.mp4"]
    assert "options" not in events[0]


def test_progress_unknown_job_404(client):
    """Checks progress for an unknown job is a 404."""
    assert client.get("/progress/" + "a" * 32).status_code == 404


def test_two_jobs_progress_independent(client, store):
    """Checks one job's progress never shows another job's state."""
    a = store.create("single", "video", {})
    b = store.create("single", "video", {})
    store.update(a.id, status="error", percent=40, error="nope")
    store.update(b.id, status="done", percent=100)
    event = read_events(client.get(f"/progress/{a.id}"))[0]
    assert event["percent"] == 40
    assert event["id"] == a.id


def test_files_are_scoped_to_job(client, store, tmp_path):
    """Checks two jobs with the same file name each serve their own file, and keep it."""
    jobs = [store.create("single", "video", {}) for _ in range(2)]
    for n, job in enumerate(jobs):
        (tmp_path / job.id / "clip.mp4").write_bytes(f"video-{n}".encode())
    for n, job in enumerate(jobs):
        response = client.get(f"/files/{job.id}/clip.mp4")
        assert response.data == f"video-{n}".encode()
        assert "attachment" in response.headers["Content-Disposition"]
        response.close()
        assert (tmp_path / job.id / "clip.mp4").exists()


def test_files_blocks_traversal_and_job_json(client, store, tmp_path):
    """Checks /files can't escape the job folder or expose job.json."""
    a = store.create("single", "video", {})
    b = store.create("single", "video", {})
    (tmp_path / b.id / "clip.mp4").write_bytes(b"secret")
    assert client.get(f"/files/{a.id}/..%2F{b.id}%2Fclip.mp4").status_code == 404
    assert client.get(f"/files/{a.id}/job.json").status_code == 404
    assert client.get(f"/files/{'f' * 32}/clip.mp4").status_code == 404


def test_jobs_lookup(client, store):
    """Checks lookup returns known jobs and null for unknown or malformed ids."""
    job = store.create("single", "audio", {})
    unknown = "e" * 32
    data = client.post("/jobs/lookup", json={"ids": [job.id, unknown, "../x"]}).get_json()
    assert data["jobs"][job.id]["dtype"] == "audio"
    assert data["jobs"][unknown] is None
    assert data["jobs"]["../x"] is None


def test_jobs_lookup_rejects_bad_requests(client):
    """Checks lookup refuses a missing list or more than 100 ids."""
    assert client.post("/jobs/lookup", json={}).status_code == 400
    assert client.post("/jobs/lookup", json={"ids": ["a" * 32] * 101}).status_code == 400


def test_delete_job(client, store, tmp_path):
    """Checks deleting a job removes it, and a second delete is a 404."""
    job = store.create("single", "video", {})
    assert client.delete(f"/jobs/{job.id}").status_code == 204
    assert not (tmp_path / job.id).exists()
    assert client.delete(f"/jobs/{job.id}").status_code == 404


def test_job_hook_states(store):
    """Checks yt-dlp and re-encode progress events map to job statuses."""
    job = store.create("single", "video", {})
    hook = webapp.make_job_hook(job.id)
    hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 4})
    assert (store.get(job.id).status, store.get(job.id).percent) == ("downloading", 25)
    hook({"status": "finished"})
    assert (store.get(job.id).status, store.get(job.id).percent) == ("processing", 100)
    hook({"status": "normalizing", "percent": 60})
    assert (store.get(job.id).status, store.get(job.id).percent) == ("optimizing", 60)


@pytest.mark.parametrize("message, expected", [
    ("ERROR: [youtube] abc123: Video unavailable", "Video unavailable"),
    ("ERROR: Unsupported URL: https://x", "Unsupported URL: https://x"),
    ("No subtitles found for that language", "No subtitles found for that language"),
])
def test_clean_error(message, expected):
    """
    Checks yt-dlp's prefixes are stripped from error messages.

    Parameters:
    message (str): The raw error text.
    expected (str): The cleaned text.
    """
    assert webapp.clean_error(Exception(message)) == expected
