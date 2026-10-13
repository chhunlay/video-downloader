"""Flask web front-end for downloader.py."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import yt_dlp
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

from downloader import (
    AUDIO_QUALITIES,
    download_media,
    download_percent,
    expand_batch,
    get_resolutions_with_sizes,
    get_subtitle_languages,
    get_video_info,
    parse_timestamp,
    subtitle_path_for,
    validate_trim,
)
from jobs import FINAL_STATUSES, JobStore
from version import __version__

app = Flask(__name__)

# Working folder for downloads until the browser saves them (kept 24 h). Outside
# the project so nothing piles up in it: the user cache on macOS, else temp.
_CACHE_ROOT = (os.path.expanduser("~/Library/Caches") if sys.platform == "darwin"
               else tempfile.gettempdir())
DOWNLOAD_DIR = os.path.join(_CACHE_ROOT, "Video Downloader")

store = JobStore(DOWNLOAD_DIR)

DTYPES = ("video", "audio", "tiktok")
SUBTITLE_MODES = ("srt", "burn")
LOOKUP_LIMIT = 100

# Language codes like "en", "en-US", "zh-Hans", "km-orig". yt-dlp treats the
# code as a regex and the code is part of the .srt file name, so nothing else is allowed.
_SUB_LANG_RE = re.compile(r"[A-Za-z0-9]{1,8}(-[A-Za-z0-9]{1,10})*")

# At most this many downloads run at once; the rest wait as "queued"
JOB_SLOTS = threading.BoundedSemaphore(2)

# New downloads are refused below this much free disk space
MIN_FREE_BYTES = 2 * 1024 ** 3

# 0.10.0 moved local downloads here; still read so those history entries keep working
SAVE_DIR = os.path.expanduser("~/Downloads")

# yt-dlp prefixes errors with "ERROR: " and often "[site] video-id: "
_ERROR_PREFIX_RE = re.compile(r"^(ERROR:\s*)?(\[[^\]]+\]\s*[^:\s]+:\s*)?")


def clean_error(e):
    """
    Turns an exception into a short message for the page.

    Parameters:
    e (Exception): The error.

    Returns:
    str: The message without yt-dlp's "ERROR: [site] id:" prefix.
    """
    return _ERROR_PREFIX_RE.sub("", str(e), count=1).strip() or "Download failed"


def finish_job(job_id, paths, **fields):
    """
    Marks a job done with its finished files, ready for the browser to fetch.

    Parameters:
    job_id (str): The job.
    paths (list): Paths of the finished files, main file first.
    **fields: Extra job fields to set (e.g. title).
    """
    if store.get(job_id) is None:
        return
    store.update(job_id, status="done", percent=100,
                 files=[os.path.basename(p) for p in paths], **fields)


def parse_download_form(form):
    """
    Validates the /download form.

    Parameters:
    form (werkzeug.datastructures.MultiDict): The posted form.

    Returns:
    dict: {"mode", "dtype", "url" or "urls", "options"}; options holds
    resolution, audio_quality, trim and subtitles (trim and subtitles are
    always None in batch mode).

    Raises:
    ValueError: With a message for the page when a field is invalid.
    """
    dtype = form.get("type")
    if dtype not in DTYPES:
        raise ValueError("Unknown download type")

    resolution = (form.get("resolution") or "").strip() or None
    if resolution and not resolution.isdigit():
        raise ValueError("Invalid resolution")

    audio_quality = form.get("audio_quality") or "192"
    if audio_quality not in AUDIO_QUALITIES:
        raise ValueError("Unknown audio quality")

    if form.get("mode") == "batch":
        # Videos differ in length and languages, so batches skip trim and subtitles
        return {
            "mode": "batch",
            "dtype": dtype,
            "urls": expand_batch(form.get("urls")),
            "options": {
                "resolution": resolution,
                "audio_quality": audio_quality,
                "trim": None,
                "subtitles": None,
            },
        }

    url = (form.get("url") or "").strip()
    if not url:
        raise ValueError("Paste a link first")

    # The real length is unknown here; yt-dlp stops at the end of the video
    trim = validate_trim(
        parse_timestamp(form.get("trim_start")), parse_timestamp(form.get("trim_end")), None
    )

    sub_mode = form.get("sub_mode") or "off"
    sub_lang = (form.get("sub_lang") or "").strip()
    subtitles = None
    if sub_mode != "off":
        if sub_mode not in SUBTITLE_MODES:
            raise ValueError("Unknown subtitle option")
        if not sub_lang:
            raise ValueError("Pick a subtitle language")
        # "all" is a yt-dlp keyword that would fetch every language
        if not _SUB_LANG_RE.fullmatch(sub_lang) or sub_lang.lower() == "all":
            raise ValueError("Unknown subtitle language")
        subtitles = {"lang": sub_lang, "mode": sub_mode}

    return {
        "mode": "single",
        "dtype": dtype,
        "url": url,
        "options": {
            "resolution": resolution,
            "audio_quality": audio_quality,
            "trim": trim,
            "subtitles": subtitles,
        },
    }


def make_job_hook(job_id, item_index=0, item_count=1):
    """
    Builds a progress hook that reports yt-dlp and re-encode progress to a job.

    For batches the item's percent is turned into the whole batch's percent.
    The job is only saved when its status or whole percent changes.

    Parameters:
    job_id (str): The job to update.
    item_index (int): Position of this item in the batch, from 0.
    item_count (int): Number of items in the batch.

    Returns:
    callable: A hook for download_media's progress_hook.
    """
    last = {"status": None, "percent": None}

    def hook(d):
        # A deleted job stops its download at the next progress update
        if store.get(job_id) is None:
            raise yt_dlp.utils.DownloadCancelled("Job deleted")
        if d["status"] == "downloading":
            status, percent = "downloading", download_percent(d)
            if percent is None:
                percent = last["percent"] or 0
        elif d["status"] == "finished":
            status, percent = "processing", 100
        elif d["status"] == "normalizing":
            status, percent = "optimizing", d.get("percent", last["percent"] or 0)
        else:
            return
        percent = int((item_index + percent / 100) / item_count * 100)
        if (status, percent) != (last["status"], last["percent"]):
            last.update(status=status, percent=percent)
            store.update(job_id, status=status, percent=percent)

    return hook


def run_single(job_id, url):
    """
    Downloads one link into its job folder, reporting through the job.

    Parameters:
    job_id (str): The job to run.
    url (str): The video link.
    """
    job = store.get(job_id)
    if job is None:
        return
    options = job.options
    store.update(job_id, status="downloading")
    try:
        trim = options.get("trim")
        path = download_media(
            url, job.dtype, store.folder(job_id),
            progress_hook=make_job_hook(job_id),
            resolution=options.get("resolution"),
            audio_quality=options.get("audio_quality", "192"),
            trim=tuple(trim) if trim else None,
            subtitles=options.get("subtitles"),
        )
        paths = [path]
        subtitles = options.get("subtitles")
        if subtitles and os.path.exists(subtitle_path_for(path, subtitles["lang"])):
            paths.append(subtitle_path_for(path, subtitles["lang"]))
        finish_job(job_id, paths, title=os.path.splitext(os.path.basename(path))[0])
    except Exception as e:
        store.update(job_id, status="error", error=clean_error(e))


def run_batch(job_id, urls):
    """
    Downloads each link in turn, then zips the results into one file.

    Each item gets its own numbered sub-folder, so videos with the same title
    don't overwrite each other; the numbers keep the playlist order in the zip.
    Failed items are skipped and listed in failed_items.

    Parameters:
    job_id (str): The batch job.
    urls (list): The video links.
    """
    job = store.get(job_id)
    if job is None:
        return
    options = job.options
    folder = store.folder(job_id)
    store.update(job_id, status="downloading", item_count=len(urls),
                 title=f"Batch ({len(urls)} items)")

    downloaded = []  # (path, name in zip)
    failed = []
    for index, url in enumerate(urls):
        if store.get(job_id) is None:
            return  # deleted; run_job removes the folder
        store.update(job_id, item_index=index)
        item_folder = os.path.join(folder, f"{index + 1:02d}")
        os.makedirs(item_folder, exist_ok=True)
        try:
            path = download_media(
                url, job.dtype, item_folder,
                progress_hook=make_job_hook(job_id, index, len(urls)),
                resolution=options.get("resolution"),
                audio_quality=options.get("audio_quality", "192"),
                trim=None,
                subtitles=None,
            )
            downloaded.append((path, f"{index + 1:02d} - {os.path.basename(path)}"))
        except Exception as e:
            failed.append({"url": url, "error": clean_error(e)})
            store.update(job_id, failed_items=list(failed))

    if store.get(job_id) is None:
        return
    if not downloaded:
        store.update(job_id, status="error", error="None of the links could be downloaded")
        return

    store.update(job_id, status="zipping")
    zip_name = f"batch-{job_id[:8]}.zip"
    try:
        # Media is already compressed, so store it as-is
        with zipfile.ZipFile(os.path.join(folder, zip_name), "w", zipfile.ZIP_STORED) as z:
            for path, arcname in downloaded:
                z.write(path, arcname)
    except Exception as e:
        store.update(job_id, status="error", error=clean_error(e))
        return
    finally:
        for index in range(len(urls)):
            shutil.rmtree(os.path.join(folder, f"{index + 1:02d}"), ignore_errors=True)

    finish_job(job_id, [os.path.join(folder, zip_name)])


def run_job(worker, job_id, target):
    """
    Runs a worker once a download slot is free, then tidies up if the job was deleted meanwhile.

    Parameters:
    worker (callable): run_single or run_batch.
    job_id (str): The job.
    target (str/list): The link (single) or links (batch).
    """
    try:
        with JOB_SLOTS:
            worker(job_id, target)
    finally:
        # yt-dlp recreates its output folder, so a deleted job can leave one behind
        if store.get(job_id) is None:
            shutil.rmtree(store.folder(job_id), ignore_errors=True)


@app.route("/")
def index():
    """
    Serves the main page.

    Returns:
    str: The rendered index.html, with the app version in the footer.
    """
    return render_template("index.html", version=__version__)


@app.route("/download", methods=["POST"])
def download():
    """
    Validates the form and starts a download job in a background thread.

    Returns:
    Response: {"job_id": ...}, 400 {"error": ...} for invalid input, or 503
    when the server is low on disk space.
    """
    try:
        parsed = parse_download_form(request.form)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if shutil.disk_usage(store.root_dir).free < MIN_FREE_BYTES:
        return jsonify({"error": "The server is low on disk space, try again later"}), 503

    job = store.create(parsed["mode"], parsed["dtype"], parsed["options"])
    if parsed["mode"] == "batch":
        worker, target = run_batch, parsed["urls"]
    else:
        worker, target = run_single, parsed["url"]
    threading.Thread(target=run_job, args=(worker, job.id, target)).start()
    return jsonify({"job_id": job.id})


@app.route("/progress/<job_id>")
def progress(job_id):
    """
    Streams one job's progress as server-sent events until it finishes.

    Parameters:
    job_id (str): The job id.

    Returns:
    Response: A text/event-stream response, or 404 for unknown jobs.
    """
    if store.get(job_id) is None:
        abort(404)

    def event_stream():
        while True:
            job = store.get(job_id)
            if job is None:
                break
            yield f"data:{json.dumps(job.public())}\n\n"
            if job.status in FINAL_STATUSES:
                break
            time.sleep(0.5)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/files/<job_id>/<path:name>")
def job_file(job_id, name):
    """
    Sends one of a job's files as a download.

    Jobs from 0.10.0 that were moved to ~/Downloads are served from there, but
    only the files they created; nothing else in Downloads is reachable.

    Parameters:
    job_id (str): The job id.
    name (str): File name inside the job folder.

    Returns:
    Response: The file, or 404 for unknown jobs or files.
    """
    job = store.get(job_id)
    if job is None or name == "job.json":
        abort(404)
    if job.saved_locally:
        if name not in job.files:
            abort(404)
        return send_from_directory(SAVE_DIR, name, as_attachment=True)
    return send_from_directory(store.folder(job_id), name, as_attachment=True)


@app.route("/jobs/lookup", methods=["POST"])
def jobs_lookup():
    """
    Reports the state of the jobs a browser remembers.

    Returns:
    Response: {"jobs": {id: job fields or null}}, or 400 for a bad request.
    """
    body = request.get_json(silent=True)
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or len(ids) > LOOKUP_LIMIT:
        return jsonify({"error": f"Send a list of at most {LOOKUP_LIMIT} ids"}), 400

    result = {}
    for job_id in ids:
        if isinstance(job_id, str):
            job = store.get(job_id)
            result[job_id] = job.public() if job else None
    return jsonify({"jobs": result})


@app.route("/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    """
    Deletes a job and its files.

    Parameters:
    job_id (str): The job id.

    Returns:
    Response: 204, or 404 if the job doesn't exist.
    """
    if not store.delete(job_id):
        abort(404)
    return "", 204


@app.route("/info", methods=["POST"])
def video_info():
    """
    Returns a video's preview details, resolutions and subtitle languages.

    Returns:
    Response: JSON with title, thumbnail, uploader, duration, resolutions and
    subtitles, or {"error": ...}.
    """
    url = request.form.get("url")

    try:
        info = get_video_info(url)

        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "resolutions": get_resolutions_with_sizes(info),
            "subtitles": get_subtitle_languages(info),
        })

    except Exception as e:
        return jsonify({"error": str(e)})


# Port 5050 because macOS AirPlay takes 5000
PORT = 5050


def start_tunnel():
    """
    Starts a temporary Cloudflare Tunnel that gives the app a public URL.

    Needs `brew install cloudflared`; it prints a random *.trycloudflare.com address.

    Returns:
    subprocess.Popen/None: The cloudflared process, or None if it isn't installed.
    """
    try:
        return subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"])
    except FileNotFoundError:
        print("cloudflared not found - running without a public link")
        return None


def stop_tunnel(tunnel):
    """
    Stops the cloudflared process, forcing it if it doesn't exit in 5 seconds.

    Parameters:
    tunnel (subprocess.Popen/None): The process from start_tunnel().
    """
    if tunnel is None:
        return
    tunnel.terminate()
    try:
        tunnel.wait(timeout=5)
    except subprocess.TimeoutExpired:
        tunnel.kill()


def main(argv=None):
    """
    Runs the web app; with --tunnel, also one public tunnel that closes when the app stops.

    Debug mode stays off: its auto-reloader re-ran this function on every code
    change, leaving a new cloudflared behind each time, and its debugger must
    never be reachable through the public link.

    Parameters:
    argv (list/None): Command-line arguments; defaults to sys.argv[1:].
    """
    parser = argparse.ArgumentParser(description="Video Downloader web app")
    parser.add_argument("--tunnel", action="store_true",
                        help="also open a public *.trycloudflare.com link (needs cloudflared)")
    args = parser.parse_args(argv)

    store.load()
    store.start_cleanup_thread()

    # `kill` (SIGTERM) normally skips finally blocks; turn it into a normal exit
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    tunnel = start_tunnel() if args.tunnel else None
    try:
        # 0.0.0.0 allows other devices on the LAN
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    finally:
        stop_tunnel(tunnel)


if __name__ == "__main__":
    main()
