"""
Shared yt-dlp download logic used by both the web app (app.py) and the
Telegram bot (telegram_bot.py) - one implementation, so fixes (TikTok
no-watermark format selection, retries for TikTok's transient scraping
errors, and correct on-disk filenames) apply to both instead of drifting
between two separate copies.
"""

import os
import time

import requests
import yt_dlp
from PIL import Image

# TikTok's anti-scraping page intermittently returns a broken page to
# yt-dlp (e.g. "Unable to extract universal data for rehydration") even
# for videos that are perfectly available a moment later - retry a few
# times before treating it as a real failure.
TRANSIENT_ERROR_HINTS = (
    "unable to extract universal data for rehydration",
    "unexpected response from webpage request",
    "please report this issue",
    "status code 100001",
)


def extract_info_with_retry(ydl, url, download, attempts=5, delay=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            message = str(e).lower()
            is_transient = any(hint in message for hint in TRANSIENT_ERROR_HINTS)
            if not is_transient or attempt == attempts:
                raise
            time.sleep(delay)
    raise last_error


def get_video_info(url):
    """Metadata-only lookup (title, thumbnail, uploader, duration, ...)."""
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        return extract_info_with_retry(ydl, url, download=False)


def make_square(image_path):
    try:
        img = Image.open(image_path).convert("RGB")

        target_size = 1920
        ratio = max(target_size / img.width, target_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

        left = (img.width - target_size) / 2
        top = (img.height - target_size) / 2
        right = (img.width + target_size) / 2
        bottom = (img.height + target_size) / 2
        img = img.crop((left, top, right, bottom))

        img.save(image_path, "JPEG", quality=100, subsampling=0)
    except Exception as e:
        print("Thumbnail processing error:", e)


def download_media(url, dtype, out_dir, progress_hook=None):
    """
    Downloads a video ("video"), audio ("audio"), or watermark-free
    TikTok clip ("tiktok") from `url` into `out_dir`.

    `progress_hook` is optional and receives yt-dlp's normal progress
    dict (status/percent/etc.) - pass one to report live progress (the
    web app does; the Telegram bot doesn't need to).

    Returns the absolute path of the final downloaded file. Raises on
    failure (typically yt_dlp.utils.DownloadError, or ValueError for a
    content type we know isn't supported - see the /photo/ check below).
    """
    if "tiktok.com" in url.lower() and "/photo/" in url.lower():
        # TikTok "Photo Mode" posts (an image slideshow + background
        # audio, not an actual video file) have no yt-dlp extractor at
        # all as of this writing - only regular /video/ posts work.
        # Fail with a clear message instead of yt-dlp's raw internal
        # "Unsupported URL" error.
        raise ValueError(
            "This is a TikTok photo/slideshow post, not a video - that "
            "format isn't supported (only regular TikTok videos are)."
        )

    os.makedirs(out_dir, exist_ok=True)

    ydl_opts = {
        'outtmpl': os.path.join(out_dir, '%(title).80s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    thumb_path = None

    # ================= GET BEST THUMBNAIL (audio only) =================
    if dtype == "audio":
        info_for_thumb = get_video_info(url)
        thumbnails = info_for_thumb.get("thumbnails", [])

        if thumbnails:
            best_thumb = max(thumbnails, key=lambda t: t.get("width", 0))
            thumb_url = best_thumb["url"]
            thumb_path = os.path.join(out_dir, "thumb_temp.jpg")

            try:
                response = requests.get(thumb_url, stream=True)
                with open(thumb_path, "wb") as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                make_square(thumb_path)
            except Exception as e:
                print("Thumbnail download failed:", e)
                thumb_path = None

    # ================= FORMAT SELECTION =================
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
                },
            ],
            'embedthumbnail': False,
            'prefer_ffmpeg': True,
        })
    elif dtype == "tiktok":
        # TikTok's watermarked stream is always exposed as the format id
        # "download" (format_note "...watermarked", lowercase - a plain
        # substring filter on "Watermark" silently missed it). Excluding
        # that id by name leaves only the clean h264/bytevc1 play formats
        # to choose from.
        ydl_opts.update({
            'format': 'best[format_id!=download]/best'
        })
    else:
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best'
        })

    # ================= DOWNLOAD =================
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = extract_info_with_retry(ydl, url, download=True)

        # prepare_filename() applies the same 80-char truncation and
        # character sanitization as the outtmpl above, so this always
        # matches what yt-dlp actually wrote to disk. Building the name
        # from the raw, untruncated title instead produced a filename
        # that didn't exist on disk for long/special-character titles.
        base_filename = os.path.basename(ydl.prepare_filename(info))
        if dtype == "audio":
            # FFmpegExtractAudio always re-muxes to mp3, regardless of
            # the original container extension yt-dlp reports here.
            filename = os.path.splitext(base_filename)[0] + ".mp3"
        else:
            filename = base_filename

    final_path = os.path.join(out_dir, filename)

    # ================= EMBED THUMBNAIL (audio only) =================
    if dtype == "audio" and thumb_path and os.path.exists(final_path):
        embed_path = os.path.join(out_dir, f"final_{filename}")

        result = os.system(f'''
        ffmpeg -y -i "{final_path}" -i "{thumb_path}" \
        -map 0:0 -map 1:0 \
        -c:a copy \
        -c:v mjpeg \
        -id3v2_version 3 \
        -metadata:s:v title="Album cover" \
        -metadata:s:v comment="Cover (front)" \
        "{embed_path}"
        ''')

        if result == 0 and os.path.exists(embed_path):
            os.remove(final_path)
            os.rename(embed_path, final_path)

    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    return final_path
