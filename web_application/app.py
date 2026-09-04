from flask import Flask, render_template, request, Response, jsonify, send_from_directory
import os
import threading
import time
import json

from downloader import download_media, get_video_info, get_available_resolutions

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
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)
        if total:
            progress_data["percent"] = int(downloaded / total * 100)
            progress_data["status"] = "downloading"

    elif d['status'] == 'finished':
        progress_data["status"] = "processing"
        progress_data["percent"] = 100

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
            "resolutions": get_available_resolutions(info),
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
    app.run(host="0.0.0.0", debug=True, port=5050)