"""
Shared yt-dlp download logic used by both the web app (app.py) and the
Telegram bot (telegram_bot.py) - one implementation, so fixes (TikTok
no-watermark format selection, retries for TikTok's transient scraping
errors, and correct on-disk filenames) apply to both instead of drifting
between two separate copies.
"""

import os
import re
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


# YouTube increasingly demands proof of a real logged-in browser session
# ("Sign in to confirm you're not a bot") for its normal ("web") client,
# even with no unusual request volume. There's no clean bypass for that -
# it's a server-side identity check, not a client-side quirk. What *does*
# still get through without any login is pretending to be the YouTube
# Android app instead of a browser - at a real cost: YouTube's SABR
# streaming restriction limits the Android client to a single legacy
# ~240p format, nothing higher, when unauthenticated. So: try the normal
# (full-quality) path first, and only fall back to the low-quality
# Android path if YouTube specifically demands sign-in.
YOUTUBE_SIGN_IN_HINT = "sign in to confirm"


def extract_info_with_fallback(ydl_opts, url, download):
    """
    Returns (info, effective_opts) - effective_opts is ydl_opts as
    actually used (possibly with the Android-client fallback merged in),
    which callers that need prepare_filename() must reuse to build a
    YoutubeDL with matching settings (outtmpl, etc.).
    """
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            return extract_info_with_retry(ydl, url, download), ydl_opts
        except yt_dlp.utils.DownloadError as e:
            if YOUTUBE_SIGN_IN_HINT not in str(e).lower():
                raise

    fallback_opts = dict(ydl_opts)
    fallback_opts['extractor_args'] = {'youtube': {'player_client': ['android']}}
    with yt_dlp.YoutubeDL(fallback_opts) as ydl:
        return extract_info_with_retry(ydl, url, download), fallback_opts


def get_video_info(url):
    """Metadata-only lookup (title, thumbnail, uploader, duration, ...)."""
    info, _ = extract_info_with_fallback({'quiet': True, 'no_warnings': True}, url, download=False)
    return info


def get_available_resolutions(info):
    """
    Returns the distinct video heights (e.g. [1080, 720, 480]) available
    for this already-fetched info dict, highest first - for populating a
    resolution picker. Empty list if the site/extractor exposes no
    height info (e.g. audio-only content, or a site yt-dlp can't inspect
    formats for).
    """
    heights = {
        f["height"]
        for f in info.get("formats", [])
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }
    return sorted(heights, reverse=True)


# Junk commonly appended to YouTube upload titles that hurts a music
# search match - stripped before querying iTunes.
_TITLE_JUNK_RE = re.compile(
    r"""[\[(][^\])]*
        (?:official|lyric|lyrics|audio|video|mv|hd|hq|4k|remaster\w*|
           visualiz\w*|explicit)
        [^\])]*[\])]
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_title_for_search(title):
    cleaned = _TITLE_JUNK_RE.sub("", title or "")
    cleaned = cleaned.replace("|", " ").strip(" -|")
    return cleaned.strip()


def get_itunes_cover_art(title):
    """
    Looks up `title` on iTunes' free public search API (no key required)
    and returns a high-resolution official cover art URL for the best
    match, or None if nothing reasonable was found (any error is treated
    the same as "no match" - this is a nice-to-have, never worth failing
    the whole download over).
    """
    query = clean_title_for_search(title)
    if not query:
        return None

    try:
        response = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "entity": "song", "limit": 1},
            timeout=8,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None

        # iTunes serves artwork at a small fixed size by default (e.g.
        # ".../100x100bb.jpg") - swap in a much larger size, which the
        # same CDN happily serves for the same image.
        artwork_url = results[0].get("artworkUrl100")
        if not artwork_url:
            return None
        return re.sub(r"\d+x\d+bb\.jpg$", "1200x1200bb.jpg", artwork_url)

    except Exception as e:
        print("iTunes cover art lookup failed:", e)
        return None


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


def download_media(url, dtype, out_dir, progress_hook=None, resolution=None):
    """
    Downloads a video ("video"), audio ("audio"), or watermark-free
    TikTok clip ("tiktok") from `url` into `out_dir`.

    `progress_hook` is optional and receives yt-dlp's normal progress
    dict (status/percent/etc.) - pass one to report live progress (the
    web app does; the Telegram bot doesn't need to).

    `resolution` (dtype == "video" only) caps the download to that
    height or less, e.g. 720 for "720p or the closest lower option this
    video actually has". None (default) downloads the best available.
    Ignored for "audio" and "tiktok" (TikTok's format selection already
    picks a specific single stream to dodge the watermark - there's
    nothing to cap).

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

        # Prefer the real, official song cover art (from iTunes) over the
        # video's own thumbnail, which is often just a still frame, a
        # lyric-video background, or otherwise unrelated to the actual
        # album art. Falls back to the video thumbnail whenever no good
        # iTunes match is found.
        thumb_url = get_itunes_cover_art(info_for_thumb.get("title", ""))

        if not thumb_url:
            thumbnails = info_for_thumb.get("thumbnails", [])
            if thumbnails:
                best_thumb = max(thumbnails, key=lambda t: t.get("width", 0))
                thumb_url = best_thumb["url"]

        if thumb_url:
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
        if resolution:
            # Cap at the chosen height; falls through to the closest
            # lower option (yt-dlp's own comparison operators) if this
            # exact video doesn't have that exact resolution.
            height_filter = f"[height<={int(resolution)}]"
        else:
            height_filter = ""
        ydl_opts.update({
            # Prefer genuinely mp4-native streams (h264 video + m4a
            # audio) first - YouTube Shorts in particular often only
            # advertises "best" as a VP9/webm pair, which downloaded as
            # a .webm file instead of the .mp4 every other button here
            # produces. Falling back to plain bestvideo+bestaudio keeps
            # this working for sites/videos with no mp4 option at all.
            'format': (
                f'bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/'
                f'best[ext=mp4]{height_filter}/'
                f'bestvideo{height_filter}+bestaudio/best{height_filter}/best'
            ),
            # Whatever combination of streams gets picked, remux the
            # final container to mp4 - guarantees a .mp4 file (and a
            # filename extension that actually matches it) even in the
            # rare case only webm-native streams exist for this video.
            'merge_output_format': 'mp4',
        })

    # ================= DOWNLOAD =================
    info, effective_opts = extract_info_with_fallback(ydl_opts, url, download=True)

    with yt_dlp.YoutubeDL(effective_opts) as ydl:
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
