"""
Shared yt-dlp download logic used by both the web app (app.py) and the
Telegram bot (telegram_bot.py) - one implementation, so fixes (TikTok
no-watermark format selection, retries for TikTok's transient scraping
errors, and correct on-disk filenames) apply to both instead of drifting
between two separate copies.
"""

import os
import re
import subprocess
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


# Instagram and Facebook, unlike TikTok's public share links, almost
# always refuse to serve video to a logged-out request at all ("sent an
# empty media response" / "Cannot parse data" from yt-dlp, even for
# posts that are visible in a browser with no login). There's no
# watermark-stripping trick that gets around this the way there is for
# TikTok - the only fix is handing yt-dlp real session cookies. Export
# them from a logged-in browser with an extension like "Get cookies.txt
# LOCALLY" (Netscape format) and save the file as cookies.txt next to
# this script. A single such file can hold cookies for multiple sites
# at once, so the same file covers Instagram, Facebook, and YouTube
# age/region-gated content together.
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def _cookie_opts():
    """{'cookiefile': COOKIE_FILE} if a real Netscape-format cookie file
    is present, else {}. Guards against a cookies.txt that exists but
    isn't actually a cookie export (yt-dlp raises a hard-to-read error
    on a malformed file instead of just ignoring it)."""
    if not os.path.isfile(COOKIE_FILE):
        return {}
    try:
        with open(COOKIE_FILE, "r", errors="ignore") as f:
            first_line = f.readline()
    except OSError:
        return {}
    if "Netscape HTTP Cookie File" not in first_line and "HTTP Cookie File" not in first_line:
        print(f"[cookies] {COOKIE_FILE} doesn't look like a Netscape cookie "
              "export - ignoring it. Instagram/Facebook links will fail "
              "without real cookies.")
        return {}
    return {"cookiefile": COOKIE_FILE}


def get_video_info(url):
    """Metadata-only lookup (title, thumbnail, uploader, duration, ...)."""
    opts = {'quiet': True, 'no_warnings': True, **_cookie_opts()}
    info, _ = extract_info_with_fallback(opts, url, download=False)
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


def download_percent(d):
    """
    Extracts an integer 0-100 percent from a yt-dlp progress-hook dict
    for status == "downloading". Returns None if the total size isn't
    known yet (e.g. the very start of the download, or a stream whose
    length yt-dlp can't predict) - callers should just skip updating on
    None rather than showing a misleading 0%.

    Shared by app.py's SSE progress endpoint and telegram_bot.py's
    message-editing progress so both report identically instead of two
    slightly different copies of the same math drifting apart.
    """
    total = d.get('total_bytes') or d.get('total_bytes_estimate')
    downloaded = d.get('downloaded_bytes', 0)
    if not total:
        return None
    return int(downloaded / total * 100)


def _get_source_fps(path):
    """Best-effort read of the source video's frame rate (e.g.
    "30000/1001") via ffprobe, so normalize_for_social() re-encodes at
    the same nominal rate instead of guessing a fixed number. Falls
    back to "30" if ffprobe is missing or can't tell (rare)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        fps = result.stdout.strip()
        return fps if fps and fps != "0/0" else "30"
    except Exception:
        return "30"


def _get_duration_seconds(path):
    """Best-effort source duration in seconds, used to turn ffmpeg's raw
    out_time into a 0-100 percent for normalize_for_social()'s progress
    reporting. Returns None if ffprobe can't tell (progress reporting is
    then just skipped - the re-encode itself is unaffected)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _already_social_safe(path):
    """
    True if `path` is already constant-frame-rate H.264/AAC/yuv420p -
    i.e. re-encoding it would only cost quality for no compatibility
    gain, so normalize_for_social() should just fast-remux it instead.

    In practice this is the exception, not the rule: YouTube and TikTok
    both serve their best-quality streams as AV1 or HEVC, neither of
    which most social apps' upload pipelines accept directly (that
    mismatch was the original "slow/choppy upload" bug this whole
    normalization step exists to fix) - so most downloads still need
    the real re-encode below. This check exists for the formats that
    *are* already safe (some lower-quality TikTok/YouTube renditions
    are plain CFR H.264), so those don't pay a quality cost for
    nothing.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,pix_fmt,r_frame_rate,avg_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        codec, pix_fmt, r_fps, avg_fps = result.stdout.split()
        return codec == "h264" and pix_fmt == "yuv420p" and r_fps == avg_fps
    except Exception:
        return False


def _get_height(path):
    """Best-effort source video height in pixels, or None if ffprobe
    can't tell. Used by normalize_for_social()'s max_height option to
    decide whether downscaling is actually needed."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def normalize_for_social(path, progress_hook=None, max_height=None):
    """
    Makes the video at `path` safe to post on Instagram/TikTok/Facebook
    without their own upload pipeline choking on it - constant frame
    rate (CFR), H.264/AAC, in an mp4 container with a fast-start moov
    atom - re-encoding only if the source doesn't already qualify.

    Two separate problems this addresses, both caused by yt-dlp's
    video+audio merge only muxing streams together (-c copy, no
    re-encode) rather than fixing them up:

    1. VFR (variable frame rate) - common on YouTube's adaptive streams
       and on re-encoded TikTok content. Every major social platform
       re-transcodes whatever you post into its own delivery format
       server-side, and those transcoders assume constant frame rate:
       fed a VFR file, they drop/duplicate frames, which is what shows
       up as choppy/stuttery playback in the posted result even though
       the file played back smoothly on-device before uploading.
    2. Codec - YouTube and TikTok's best-quality streams are typically
       AV1 or HEVC, not H.264. Most social apps' upload pipelines only
       handle H.264 well; handed anything else, the app does its own
       (slow, sometimes janky) client-side transcode before it'll even
       accept the upload - this was the original "upload is slow"
       complaint that led to this whole function existing.

    If the source is already CFR H.264/AAC/yuv420p (checked by
    _already_social_safe()), neither problem applies, so this just
    fast-remuxes it (+faststart only, no re-encode - zero quality
    loss) instead of needlessly recompressing an already-fine file.
    Otherwise it does the full re-encode, which is unavoidably lossy
    to some degree (video has to be decoded and recompressed to change
    codec/frame timing at all) - CRF 18 keeps that loss close to
    visually unnoticeable at the cost of a larger file than CRF 20 was.

    If given, `progress_hook` receives {"status": "normalizing",
    "percent": int} updates as it runs (parsed from ffmpeg's own
    -progress stream) - a full re-encode, in particular, can take
    longer than the original download on a long video, and a caller
    with no visibility into that otherwise looks stuck.

    `max_height`, if given, downscales video taller than that (e.g.
    "480") during the re-encode - unlike `download_media`'s own
    `resolution` cap (which only affects yt-dlp's format *selection*
    and does nothing for TikTok, whose format string has no height
    filter at all), this applies regardless of source or platform,
    since it runs after the file already exists. It exists for testing
    on a slow connection, not normal use: a smaller file uploads
    proportionally faster on a bandwidth-limited link. It forces the
    full re-encode path even for an otherwise-already-safe source,
    since downscaling requires decoding and re-encoding regardless.

    Best-effort: if ffmpeg fails or isn't installed, the original file
    is left untouched rather than failing the whole download over what
    is otherwise a perfectly usable file.
    """
    needs_downscale = False
    if max_height:
        height = _get_height(path)
        needs_downscale = height is not None and height > int(max_height)

    if _already_social_safe(path) and not needs_downscale:
        tmp_path = path + ".faststart.mp4"
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-c", "copy", "-movflags", "+faststart", tmp_path],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, path)
            elif os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            print("Fast-start remux failed, keeping original file:", e)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        if progress_hook:
            progress_hook({"status": "normalizing", "percent": 100})
        return

    fps = _get_source_fps(path)
    duration = _get_duration_seconds(path)
    tmp_path = path + ".normalized.mp4"

    scale_args = (
        ["-vf", f"scale=-2:'min(ih,{int(max_height)})'"] if needs_downscale else []
    )

    try:
        process = subprocess.Popen(
            [
                "ffmpeg", "-y", "-i", path,
                "-r", fps, "-vsync", "cfr",
                *scale_args,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats",
                tmp_path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        if progress_hook and duration:
            last_percent = -1
            for line in process.stdout:
                if not line.startswith("out_time_ms="):
                    continue
                try:
                    out_time_ms = int(line.strip().split("=", 1)[1])
                except ValueError:
                    continue
                percent = min(99, int(out_time_ms / 1_000_000 / duration * 100))
                if percent != last_percent:
                    last_percent = percent
                    progress_hook({"status": "normalizing", "percent": percent})

        _, stderr = process.communicate(timeout=600)

        if process.returncode == 0 and os.path.exists(tmp_path):
            os.replace(tmp_path, path)
            if progress_hook:
                progress_hook({"status": "normalizing", "percent": 100})
        else:
            print(
                "Social-media normalization failed, keeping original file:",
                (stderr or "")[-500:],
            )
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception as e:
        print("Social-media normalization failed, keeping original file:", e)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


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
        **_cookie_opts(),
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

    # ================= NORMALIZE FOR SOCIAL (video/tiktok only) =========
    # yt-dlp's own "finished" progress hook already fired by this point
    # (it fires right after the download, before this function does any
    # of its own post-processing), so the web UI is already showing its
    # "processing" state while this re-encode runs.
    if dtype in ("video", "tiktok") and os.path.exists(final_path):
        # `resolution` only affects yt-dlp's format *selection* above,
        # which does nothing for TikTok (no height filter in its format
        # string) - passing it here too makes it a real cap regardless
        # of platform, since this runs after the file already exists.
        normalize_for_social(final_path, progress_hook=progress_hook, max_height=resolution)

    return final_path
