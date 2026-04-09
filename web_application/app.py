from flask import Flask, render_template, request, Response, jsonify, send_from_directory
import yt_dlp
import os
import threading
import time
import json

from PIL import Image
import requests

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


def make_square(image_path):
    try:
        img = Image.open(image_path).convert("RGB")

        target_size = 1920

        # 🔥 Resize FIRST to cover square
        ratio = max(target_size / img.width, target_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))

        img = img.resize(new_size, Image.LANCZOS)

        # 🔥 Then crop center
        left = (img.width - target_size) / 2
        top = (img.height - target_size) / 2
        right = (img.width + target_size) / 2
        bottom = (img.height + target_size) / 2

        img = img.crop((left, top, right, bottom))

        # 🔥 Save high quality
        img.save(image_path, "JPEG", quality=100, subsampling=0)

    except Exception as e:
        print("Thumbnail processing error:", e)


def download_task(url, dtype):
    progress_data["percent"] = 0
    progress_data["status"] = "starting"
    progress_data["filename"] = ""

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title).80s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True
    }

    try:
        thumb_path = None
        title = "audio"

        # ================= GET BEST THUMBNAIL =================
        if dtype == "audio":
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False)

            title = info.get("title", "audio")

            thumbnails = info.get("thumbnails", [])
            print("=== THUMBNAIL DEBUG ===")
            print("Number of thumbnails found:", len(thumbnails))
            print("thumb_path:", thumb_path)

            if thumbnails:
                best_thumb = max(thumbnails, key=lambda t: t.get("width", 0))

                thumb_url = best_thumb["url"]
                # thumb_path = os.path.join(DOWNLOAD_DIR, f"{title}.jpg")
                thumb_path = os.path.join(DOWNLOAD_DIR, "thumb_temp.jpg")

                response = requests.get(thumb_url, stream=True)
                try:
                    response = requests.get(thumb_url, stream=True)
                    with open(thumb_path, "wb") as f:
                        for chunk in response.iter_content(1024):
                            f.write(chunk)
                    print("=== THUMB SAVED ===", thumb_path)
                except Exception as e:
                    print("=== THUMB DOWNLOAD FAILED ===", e)
                    thumb_path = None

                # 🔥 make it perfect 1920x1920
                make_square(thumb_path)

        # ================= ORIGINAL DOWNLOAD =================
        if dtype == "audio":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'writethumbnail': False,
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    },
                    {
                        'key': 'FFmpegMetadata',
                    }
                ],
                'embedthumbnail': False,
                'prefer_ffmpeg': True,
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo+bestaudio/best'
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if dtype == "audio":
                filename = f"{info.get('title', 'audio')}.mp3"
            else:
                filename = f"{info.get('title', 'video')}.mp4"

            progress_data["filename"] = filename

        # ================= EMBED THUMBNAIL =================
        if dtype == "audio" and thumb_path:
            mp3_path = os.path.join(DOWNLOAD_DIR, filename)
            final_path = os.path.join(DOWNLOAD_DIR, f"final_{filename}")

            # if os.path.exists(mp3_path):
            #     os.system(f'''
            #     ffmpeg -y -i "{mp3_path}" -i "{thumb_path}" \
            #     -map 0:0 -map 1:0 \
            #     -c:a copy \
            #     -c:v mjpeg \
            #     -id3v2_version 3 \
            #     -metadata:s:v title="Album cover" \
            #     -metadata:s:v comment="Cover (front)" \
            #     "{final_path}"
            #     ''')

            #     os.remove(mp3_path)
            #     os.rename(final_path, mp3_path)

            if os.path.exists(mp3_path):
                print("=== STARTING FFMPEG ===")
                print("MP3:", mp3_path)
                print("Thumb:", thumb_path)
                print("Final:", final_path)
                print("Thumb exists:", os.path.exists(thumb_path))

                result = os.system(f'''
                ffmpeg -y -i "{mp3_path}" -i "{thumb_path}" \
                -map 0:0 -map 1:0 \
                -c:a copy \
                -c:v mjpeg \
                -id3v2_version 3 \
                -metadata:s:v title="Album cover" \
                -metadata:s:v comment="Cover (front)" \
                "{final_path}"
                ''')

                print("FFMPEG result code:", result)
                print("Final file exists:", os.path.exists(final_path))

                os.remove(mp3_path)
                os.rename(final_path, mp3_path)

            # Remove thumbnail after embedding
            if os.path.exists(thumb_path):
                os.remove(thumb_path)


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

        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration")
        })

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(debug=True)