from flask import Flask, render_template, request, Response, jsonify, send_from_directory
import yt_dlp
import os
import threading
import time
import json

app = Flask(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Shared progress data
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
        progress_data["status"] = "done"
        progress_data["percent"] = 100
        progress_data["filename"] = os.path.basename(d.get('filename', 'file.mp4'))
    elif d['status'] == 'error':
        progress_data["status"] = f"error"

def download_task(url, dtype):
    progress_data["percent"] = 0
    progress_data["status"] = "starting"
    progress_data["filename"] = ""

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True
    }

    if dtype == "audio":
        ydl_opts.update({
            'format': 'bestaudio',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        })
    else:
        ydl_opts.update({'format': 'bestvideo+bestaudio/best'})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        progress_data["status"] = "done"
    except Exception as e:
        progress_data["status"] = f"error: {e}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    dtype = request.form.get("type")
    thread = threading.Thread(target=download_task, args=(url, dtype))
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
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route("/info", methods=["POST"])
def video_info():
    url = request.form.get("url")
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        data = {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration")
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(debug=True)
