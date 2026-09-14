from flask import Flask, render_template, request, Response, jsonify, send_from_directory, after_this_request
import os
import threading
import time
import json
import subprocess

from downloader import download_media, get_video_info, get_resolutions_with_sizes, download_percent

app = Flask(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

progress_data = {
    "percent": 0,
    "status": "idle",
    "filename": ""
}


def progress_hook(d):
    if d['status'] == 'downloading':
        percent = download_percent(d)
        if percent is not None:
            progress_data["percent"] = percent
            progress_data["status"] = "downloading"

    elif d['status'] == 'finished':
        progress_data["status"] = "processing"
        progress_data["percent"] = 100

    elif d['status'] == 'normalizing':
        # The post-download CFR re-encode (see downloader.py) - on a
        # longer video this can take longer than the download itself,
        # so it gets its own visible progress instead of the UI just
        # sitting at "processing" with no feedback.
        progress_data["status"] = "optimizing for social media"
        progress_data["percent"] = d.get("percent", progress_data["percent"])

    elif d['status'] == 'error':
        progress_data["status"] = "error"


def download_task(url, dtype, resolution=None):
    progress_data["percent"] = 0
    progress_data["status"] = "starting"
    progress_data["filename"] = ""

    try:
        final_path = download_media(
            url, dtype, DOWNLOAD_DIR, progress_hook=progress_hook, resolution=resolution
        )
        progress_data["filename"] = os.path.basename(final_path)
        progress_data["status"] = "done"

    except Exception as e:
        progress_data["status"] = f"error: {str(e)}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    dtype = request.form.get("type")
    resolution = request.form.get("resolution")  # e.g. "720", or absent for best available

    thread = threading.Thread(target=download_task, args=(url, dtype, resolution))
    thread.start()

    return jsonify({"started": True})


@app.route("/progress")
def progress():
    def event_stream():
        while True:
            yield f"data:{json.dumps(progress_data)}\n\n"

            if progress_data["status"] in ("done",) or progress_data["status"].startswith("error"):
                break

            time.sleep(0.5)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/downloads/<path:filename>")
def download_file(filename):
    # ?inline=1 serves the file for in-browser playback instead of
    # forcing a raw file download. On iOS Safari specifically, playing
    # a video inline gives access to the native Share sheet's "Save
    # Video" action, which saves straight to Photos - a forced download
    # instead lands in the Files app with no direct path to the photo
    # library. Desktop/default behavior (a normal download) is unchanged.
    inline = request.args.get("inline") == "1"

    @after_this_request
    def cleanup(response):
        # The browser download (or auto-download from the frontend) is
        # the only consumer of this file - once it's been handed off,
        # delete it from disk so completed downloads don't pile up
        # forever in DOWNLOAD_DIR.
        try:
            os.remove(os.path.join(DOWNLOAD_DIR, filename))
        except OSError:
            pass
        return response

    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=not inline)


@app.route("/info", methods=["POST"])
def video_info():
    url = request.form.get("url")

    try:
        info = get_video_info(url)

        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "resolutions": get_resolutions_with_sizes(info),
        })

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    # Port 5000 collides with macOS AirPlay Receiver, which grabs it by
    # default and returns 403 to browser requests before Flask ever sees
    # them - use 5050 instead to avoid the conflict.
    # host="0.0.0.0" binds every network interface, not just localhost,
    # so other devices on the same LAN (e.g. a phone on the same WiFi)
    # can reach this by the Mac's local IP - fine for trusted home/office
    # networks, but note anyone else on that network can reach it too.

    # Uncomment to expose this app over the internet via a Cloudflare
    # Tunnel (requires `brew install cloudflared`). This runs a quick
    # ephemeral tunnel and prints a random *.trycloudflare.com URL to
    # the terminal - no Cloudflare account or DNS setup needed.
    subprocess.Popen(["cloudflared", "tunnel", "--url", "http://localhost:5050"])

    app.run(host="0.0.0.0", debug=True, port=5050)