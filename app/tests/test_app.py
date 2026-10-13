"""Tests for the Flask routes in app.py, with downloads faked out."""

import json
import os
import threading
import time

import pytest
import yt_dlp

import app as webapp
from jobs import JobStore

# Captured before any fixture swaps threading.Thread for InlineThread
RealThread = threading.Thread

# A visitor on another device; downloads from this Mac go to ~/Downloads instead
REMOTE_VISITOR = "203.0.113.9"


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

    def no_real_downloads(*args, **kwargs):
        raise AssertionError("a test reached the real download_media")

    # Tests that download replace this with a fake; the rest must never hit the network
    monkeypatch.setattr(webapp, "download_media", no_real_downloads)
    # Never touch the real ~/Downloads
    monkeypatch.setattr(webapp, "SAVE_DIR", str(tmp_path / "Downloads"))
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
    return client.post("/download", data=data, environ_base={"REMOTE_ADDR": REMOTE_VISITOR})


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
    assert data["subtitles"] == [{"code": "km", "name": "Khmer", "auto": False, "translated": False}]


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


# ---------- Batch ----------

def fake_batch_download(fail_urls=(), progress=None):
    """
    Builds a stand-in for download_media that saves every link as same-named "Video.mp4".

    Parameters:
    fail_urls (tuple): Links that raise instead of downloading.
    progress (dict/None): {url: callable(progress_hook)} run during that link's download.

    Returns:
    callable: The fake.
    """
    def fake(url, dtype, out_dir, progress_hook=None, **kwargs):
        if progress and url in progress:
            progress[url](progress_hook)
        if url in fail_urls:
            raise yt_dlp.utils.DownloadError(f"ERROR: [generic] {url[-1]}: Video unavailable")
        path = os.path.join(out_dir, "Video.mp4")
        with open(path, "w") as f:
            f.write(url)
        return path
    return fake


def start_batch(client, links):
    """
    Posts a batch download.

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    links (list): The links, sent one per line.

    Returns:
    flask.Response: The /download response.
    """
    return client.post("/download", data={"mode": "batch", "type": "video", "urls": "\n".join(links)},
                       environ_base={"REMOTE_ADDR": REMOTE_VISITOR})


def zip_contents(store, job):
    """
    Reads a finished batch job's zip.

    Parameters:
    store (JobStore): The store.
    job (Job): The batch job.

    Returns:
    dict: {name in zip: file text}.
    """
    import zipfile
    with zipfile.ZipFile(os.path.join(store.folder(job.id), job.files[0])) as z:
        return {name: z.read(name).decode() for name in z.namelist()}


def test_batch_zips_all_items(client, store, monkeypatch):
    """Checks every link ends up in one zip, numbered in order, with no loose files left."""
    monkeypatch.setattr(webapp, "download_media", fake_batch_download())
    job_id = start_batch(client, ["https://a", "https://b", "https://c"]).get_json()["job_id"]
    job = store.get(job_id)
    assert job.status == "done"
    assert job.files == [f"batch-{job_id[:8]}.zip"]
    assert job.item_count == 3
    assert job.title == "Batch (3 items)"
    assert zip_contents(store, job) == {
        "01 - Video.mp4": "https://a", "02 - Video.mp4": "https://b", "03 - Video.mp4": "https://c",
    }
    assert sorted(os.listdir(store.folder(job_id))) == [f"batch-{job_id[:8]}.zip", "job.json"]


def test_batch_partial_failure(client, store, monkeypatch):
    """Checks a broken link is skipped and reported while the rest are zipped."""
    monkeypatch.setattr(webapp, "download_media", fake_batch_download(fail_urls=("https://b",)))
    job_id = start_batch(client, ["https://a", "https://b", "https://c"]).get_json()["job_id"]
    job = store.get(job_id)
    assert job.status == "done"
    assert sorted(zip_contents(store, job)) == ["01 - Video.mp4", "03 - Video.mp4"]
    assert job.failed_items == [{"url": "https://b", "error": "Video unavailable"}]


def test_batch_all_fail(client, store, monkeypatch):
    """Checks a batch where nothing downloaded ends as an error."""
    monkeypatch.setattr(webapp, "download_media",
                        fake_batch_download(fail_urls=("https://a", "https://b")))
    job_id = start_batch(client, ["https://a", "https://b"]).get_json()["job_id"]
    job = store.get(job_id)
    assert job.status == "error"
    assert job.files == []


def test_batch_too_many_links(client, tmp_path):
    """Checks more than 50 links is refused before any job is made."""
    response = start_batch(client, [f"https://v{i}" for i in range(51)])
    assert response.status_code == 400
    assert "max 50" in response.get_json()["error"]
    assert os.listdir(tmp_path) == []


def test_batch_ignores_trim_and_subtitles(client, monkeypatch):
    """Checks batch mode never trims or adds subtitles, but keeps quality settings."""
    calls = []

    def fake(url, dtype, out_dir, **kwargs):
        calls.append(kwargs)
        return fake_batch_download()(url, dtype, out_dir)

    monkeypatch.setattr(webapp, "download_media", fake)
    client.post("/download", data={
        "mode": "batch", "type": "audio", "urls": "https://a", "audio_quality": "320",
        "trim_start": "0:10", "sub_lang": "km", "sub_mode": "burn",
    })
    assert calls[0]["trim"] is None
    assert calls[0]["subtitles"] is None
    assert calls[0]["audio_quality"] == "320"


def test_batch_progress_overall(client, store, monkeypatch):
    """Checks progress is reported across the whole batch, not per item."""
    seen = {}

    def halfway(hook):
        hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        job = next(iter(store._jobs.values()))
        seen.update(percent=job.percent, item_index=job.item_index)

    monkeypatch.setattr(webapp, "download_media",
                        fake_batch_download(progress={"https://b": halfway}))
    start_batch(client, ["https://a", "https://b", "https://c", "https://d"])
    assert seen == {"percent": 37, "item_index": 1}


# ---------- Review fixes ----------

@pytest.mark.parametrize("lang", [".*", "../x", "all", "en/../../a", "-en"])
def test_download_rejects_bad_subtitle_language(client, tmp_path, lang):
    """
    Checks subtitle languages must look like language codes.

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    tmp_path (pathlib.Path): The store's folder.
    lang (str): The bad code.
    """
    response = start(client, sub_lang=lang, sub_mode="srt")
    assert response.status_code == 400
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("lang", ["en", "en-US", "zh-Hans", "km-orig"])
def test_download_accepts_real_subtitle_languages(client, monkeypatch, lang):
    """
    Checks real language codes are accepted.

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.
    lang (str): The code.
    """
    monkeypatch.setattr(webapp, "download_media", fake_download())
    assert start(client, sub_lang=lang, sub_mode="srt").status_code == 200


def test_batch_bad_playlist_is_400(client, tmp_path, monkeypatch):
    """Checks a broken playlist link gives a readable 400, not a server error."""
    def broken(url):
        raise yt_dlp.utils.DownloadError("ERROR: playlist does not exist")
    monkeypatch.setattr(webapp_downloader(), "_extract_playlist", broken)
    response = start_batch(client, ["https://a.com/v1", "https://youtube.com/playlist?list=X"])
    assert response.status_code == 400
    assert "Couldn't read playlist" in response.get_json()["error"]


def webapp_downloader():
    """
    Gets the downloader module the app uses.

    Returns:
    module: downloader.
    """
    import downloader
    return downloader


def test_lookup_rejects_non_object_body(client):
    """Checks a JSON list body is a 400, not a server error."""
    assert client.post("/jobs/lookup", json=[1, 2]).status_code == 400


def test_delete_during_download_stops_and_cleans_up(client, store, tmp_path, monkeypatch):
    """Checks deleting a running job stops the download and leaves no folder behind."""
    def fake(url, dtype, out_dir, progress_hook=None, **kwargs):
        job_id = os.path.basename(out_dir)
        client.delete(f"/jobs/{job_id}")
        progress_hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        os.makedirs(out_dir, exist_ok=True)  # yt-dlp would recreate the folder
        raise AssertionError("download should have been cancelled")

    monkeypatch.setattr(webapp, "download_media", fake)
    start(client)
    assert os.listdir(tmp_path) == []


def test_delete_during_batch_skips_remaining_items(client, store, tmp_path, monkeypatch):
    """Checks a deleted batch stops before the next item and removes its folder."""
    calls = []

    def fake(url, dtype, out_dir, progress_hook=None, **kwargs):
        calls.append(url)
        job_id = os.path.basename(os.path.dirname(out_dir))
        client.delete(f"/jobs/{job_id}")
        return fake_batch_download()(url, dtype, out_dir)

    monkeypatch.setattr(webapp, "download_media", fake)
    start_batch(client, ["https://a", "https://b", "https://c"])
    assert calls == ["https://a"]
    assert os.listdir(tmp_path) == []


def test_worker_for_missing_job_does_nothing(store):
    """Checks a worker whose job was deleted before it started just returns."""
    webapp.run_single("a" * 32, "https://x")
    webapp.run_batch("b" * 32, ["https://x"])


def test_jobs_run_at_most_two_at_a_time(store, monkeypatch):
    """Checks extra downloads wait their turn instead of all running at once."""
    monkeypatch.setattr(webapp.threading, "Thread", RealThread)
    monkeypatch.setattr(webapp, "JOB_SLOTS", threading.BoundedSemaphore(2))
    running, peak, lock = [0], [0], threading.Lock()

    def fake(url, dtype, out_dir, **kwargs):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return fake_download()(url, dtype, out_dir)

    monkeypatch.setattr(webapp, "download_media", fake)
    webapp.app.config["TESTING"] = True
    client = webapp.app.test_client()
    ids = [start(client).get_json()["job_id"] for _ in range(4)]
    deadline = time.time() + 5
    while time.time() < deadline and not all(store.get(i).status == "done" for i in ids):
        time.sleep(0.05)
    assert all(store.get(i).status == "done" for i in ids)
    assert peak[0] == 2


def test_download_refused_when_disk_is_low(client, tmp_path, monkeypatch):
    """Checks new downloads are refused while free disk space is low."""
    monkeypatch.setattr(webapp, "download_media", fake_download())
    usage = type("U", (), {"free": 100 * 1024 * 1024})()
    monkeypatch.setattr(webapp.shutil, "disk_usage", lambda path: usage)
    response = start(client)
    assert response.status_code == 503
    assert os.listdir(tmp_path) == []


# ---------- Startup and tunnel ----------

class FakeTunnel:
    """Stands in for the cloudflared process."""

    def __init__(self, args, **kwargs):
        """
        Records the command.

        Parameters:
        args (list): The command line.
        **kwargs: Ignored Popen options.
        """
        self.args = args
        self.terminated = False

    def terminate(self):
        """Marks the tunnel as stopped."""
        self.terminated = True

    def wait(self, timeout=None):
        """
        Pretends the process exited.

        Parameters:
        timeout (float/None): Ignored.

        Returns:
        int: 0.
        """
        return 0


@pytest.fixture
def fake_startup(monkeypatch, store):
    """
    Fakes cloudflared, the web server and signal setup so main() can run in a test.

    Parameters:
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.
    store (JobStore): The temp store fixture.

    Returns:
    dict: "tunnels" (FakeTunnel list), "run" (app.run kwargs) and "signals" (handlers set).
    """
    seen = {"tunnels": [], "run": {}, "signals": {}}

    def fake_popen(args, **kwargs):
        tunnel = FakeTunnel(args, **kwargs)
        seen["tunnels"].append(tunnel)
        return tunnel

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.app, "run", lambda **kwargs: seen["run"].update(kwargs))
    monkeypatch.setattr(webapp.signal, "signal",
                        lambda signum, handler: seen["signals"].__setitem__(signum, handler))
    monkeypatch.setattr(store, "start_cleanup_thread", lambda: None)
    return seen


def test_main_runs_without_debug_or_reloader(fake_startup):
    """Checks the server runs with no debugger and no auto-reloader, which spawned extra tunnels."""
    webapp.main([])
    assert fake_startup["run"]["debug"] is False
    assert fake_startup["run"]["use_reloader"] is False
    assert fake_startup["run"]["port"] == 5050


def test_main_starts_no_tunnel_by_default(fake_startup):
    """Checks the public link only opens when asked for."""
    webapp.main([])
    assert fake_startup["tunnels"] == []
    assert fake_startup["run"]["port"] == 5050


def test_main_starts_one_tunnel_and_stops_it(fake_startup):
    """Checks --tunnel starts exactly one tunnel and it is closed when the server stops."""
    webapp.main(["--tunnel"])
    assert len(fake_startup["tunnels"]) == 1
    assert fake_startup["tunnels"][0].args == ["cloudflared", "tunnel", "--url", "http://localhost:5050"]
    assert fake_startup["tunnels"][0].terminated


def test_main_stops_tunnel_on_ctrl_c(fake_startup, monkeypatch):
    """Checks Ctrl+C still closes the tunnel."""
    def interrupted(**kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(webapp.app, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        webapp.main(["--tunnel"])
    assert fake_startup["tunnels"][0].terminated


def test_sigterm_exits_cleanly(fake_startup):
    """Checks `kill` turns into a normal exit, so the tunnel cleanup runs."""
    webapp.main(["--tunnel"])
    handler = fake_startup["signals"][webapp.signal.SIGTERM]
    with pytest.raises(SystemExit):
        handler(webapp.signal.SIGTERM, None)


def test_main_without_cloudflared_still_serves(fake_startup, monkeypatch):
    """Checks a missing cloudflared only skips the public link."""
    def missing(args, **kwargs):
        raise FileNotFoundError("cloudflared")
    monkeypatch.setattr(webapp.subprocess, "Popen", missing)
    webapp.main(["--tunnel"])
    assert fake_startup["run"]["port"] == 5050


# ---------- Browser downloads and the working folder ----------

def start_local(client, **form):
    """
    Posts a single download from this Mac (localhost, not through the tunnel).

    Parameters:
    client (flask.testing.FlaskClient): The test client.
    **form: Extra form fields.

    Returns:
    flask.Response: The /download response.
    """
    data = {"url": "https://x", "type": "video", **form}
    return client.post("/download", data=data, environ_base={"REMOTE_ADDR": "127.0.0.1"})


def test_local_download_is_fetched_by_the_browser(client, store, tmp_path, monkeypatch):
    """Checks a download on this Mac waits in its job folder so the browser can save it (and show its popup)."""
    monkeypatch.setattr(webapp, "download_media", fake_download())
    job_id = start_local(client).get_json()["job_id"]
    job = store.get(job_id)
    assert job.saved_locally is False
    assert "clip.mp4" in os.listdir(store.folder(job_id))
    assert not (tmp_path / "Downloads").exists()
    assert client.get(f"/files/{job_id}/clip.mp4").data == b"bytes-clip.mp4"


def test_working_folder_is_outside_the_project():
    """Checks downloads no longer pile up inside the project's app/downloads folder."""
    app_dir = os.path.dirname(os.path.abspath(webapp.__file__))
    assert not os.path.abspath(webapp.DOWNLOAD_DIR).startswith(app_dir + os.sep)


def test_files_still_serves_jobs_saved_to_downloads_by_0_10_0(client, store, tmp_path):
    """Checks history entries from 0.10.0 (files in ~/Downloads) still work, and nothing else there is reachable."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "clip.mp4").write_bytes(b"old-version")
    (downloads / "private.pdf").write_bytes(b"secret")
    job = store.create("single", "video", {})
    store.update(job.id, status="done", saved_locally=True, files=["clip.mp4"])
    assert client.get(f"/files/{job.id}/clip.mp4").data == b"old-version"
    assert client.get(f"/files/{job.id}/private.pdf").status_code == 404
