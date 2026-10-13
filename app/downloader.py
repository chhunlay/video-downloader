"""Shared yt-dlp download logic for the web app (app.py) and the Telegram bot."""

import os
import re
import subprocess
import time

import requests
import yt_dlp
from PIL import Image

# TikTok's scraper protection sometimes fails a request that works seconds later
TRANSIENT_ERROR_HINTS = (
    "unable to extract universal data for rehydration",
    "unexpected response from webpage request",
    "please report this issue",
    "status code 100001",
)

# YouTube asks for sign-in on its web client; the Android client still works,
# but only at ~240p, so it is used as a fallback only
YOUTUBE_SIGN_IN_HINT = "sign in to confirm"

# Netscape-format cookies for Instagram/Facebook/age-gated YouTube
# (export with a browser extension like "Get cookies.txt LOCALLY")
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

# TikTok always serves its watermarked stream under this format id
TIKTOK_WATERMARK_FORMAT_ID = "download"

# MP3 bitrates in kbps, or "m4a" for AAC in an m4a file
AUDIO_QUALITIES = ("128", "192", "320", "m4a")

# Bracketed junk in YouTube titles ("[Official Video]", "(Lyrics)") that hurts iTunes matching
_TITLE_JUNK_RE = re.compile(
    r"""[\[(][^\])]*
        (?:official|lyric|lyrics|audio|video|mv|hd|hq|4k|remaster\w*|
           visualiz\w*|explicit)
        [^\])]*[\])]
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------- URL helpers ----------

def is_tiktok(url):
    """
    Checks whether a link points to TikTok.

    Parameters:
    url (str): The link to check.

    Returns:
    bool: True for tiktok.com links.
    """
    return "tiktok.com" in url.lower()


# ---------- yt-dlp ----------

def extract_info_with_retry(ydl, url, download, attempts=5, delay=3):
    """
    Runs ydl.extract_info, retrying TikTok's transient scraping errors.

    Parameters:
    ydl (yt_dlp.YoutubeDL): The downloader to use.
    url (str): The video link.
    download (bool): True to download, False for metadata only.
    attempts (int): Maximum number of tries.
    delay (int/float): Seconds to wait between tries.

    Returns:
    dict: yt-dlp's info dict.

    Raises:
    yt_dlp.utils.DownloadError: On a non-transient error, or after the last try.
    """
    for attempt in range(1, attempts + 1):
        try:
            return ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as e:
            message = str(e).lower()
            is_transient = any(hint in message for hint in TRANSIENT_ERROR_HINTS)
            if not is_transient or attempt == attempts:
                raise
            time.sleep(delay)


def extract_info_with_fallback(ydl_opts, url, download):
    """
    Runs extract_info, retrying with YouTube's Android client if sign-in is required.

    Parameters:
    ydl_opts (dict): yt-dlp options.
    url (str): The video link.
    download (bool): True to download, False for metadata only.

    Returns:
    tuple: (info dict, the options actually used). Reuse those options for
    prepare_filename() so the filename matches.
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


def _cookie_opts():
    """
    Builds the yt-dlp cookie option when cookies.txt is a real Netscape export.

    Returns:
    dict: {'cookiefile': COOKIE_FILE}, or {} if the file is missing or invalid.
    """
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
    """
    Fetches a video's metadata without downloading it.

    Parameters:
    url (str): The video link.

    Returns:
    dict: yt-dlp's info dict (title, thumbnail, uploader, duration, formats, ...).
    """
    opts = {'quiet': True, 'no_warnings': True, **_cookie_opts()}
    info, _ = extract_info_with_fallback(opts, url, download=False)
    return info


def download_percent(d):
    """
    Converts a yt-dlp "downloading" progress dict to a percentage.

    Parameters:
    d (dict): yt-dlp progress dict.

    Returns:
    int/None: 0-100, or None while the total size is still unknown.
    """
    total = d.get('total_bytes') or d.get('total_bytes_estimate')
    if not total:
        return None
    return int(d.get('downloaded_bytes', 0) / total * 100)


# ---------- Batch ----------

BATCH_LIMIT = 50


def _extract_playlist(url):
    """
    Lists a playlist's videos without downloading anything.

    Parameters:
    url (str): The playlist link.

    Returns:
    dict: yt-dlp's flat info dict, with "entries".
    """
    opts = {
        'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist',
        # One past the limit is enough to know it's too long; huge playlists stay fast
        'playlistend': BATCH_LIMIT + 1,
        **_cookie_opts(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def expand_batch(text, limit=BATCH_LIMIT, extract=None):
    """
    Turns pasted links, and any playlist links among them, into a list of video links.

    Parameters:
    text (str): Links separated by spaces or new lines.
    limit (int): Maximum number of videos.
    extract (callable/None): Playlist lister, url -> info dict; defaults to yt-dlp.

    Returns:
    list: Unique video links, in the order given.

    Raises:
    ValueError: If there are no links, a playlist can't be read, or there
        are more than limit links after expansion.
    """
    extract = extract or _extract_playlist
    urls = []
    for word in (text or "").split():
        if not re.match(r"https?://", word, re.IGNORECASE):
            continue
        # A video opened from a playlist keeps its v= and stays one video
        is_playlist = "/playlist" in word or ("list=" in word and not re.search(r"[?&]v=", word))
        if is_playlist:
            try:
                entries = extract(word).get("entries") or []
            except Exception:
                raise ValueError(f"Couldn't read playlist: {word}")
            urls.extend(e.get("url") or e.get("webpage_url") for e in entries if e)
        else:
            urls.append(word)

    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls:
        raise ValueError("No links found")
    if len(urls) > limit:
        raise ValueError(f"Too many links: {len(urls)} (max {limit})")
    return urls


# ---------- Formats and sizes ----------

def _is_video_format(f):
    """
    Checks whether a format is real video.

    X/Twitter formats leave vcodec unset, so only the string "none" means no video.

    Parameters:
    f (dict): A yt-dlp format.

    Returns:
    bool: True if it has a height and isn't marked as no-video.
    """
    return bool(f.get("height")) and f.get("vcodec") != "none"


def known_size(f):
    """
    Gets a format's reported size.

    Parameters:
    f (dict): A yt-dlp format.

    Returns:
    int/None: Size in bytes, or None if not reported.
    """
    return f.get("filesize") or f.get("filesize_approx")


def format_size(num_bytes):
    """
    Formats a byte count for display.

    Parameters:
    num_bytes (int/float/None): Size in bytes.

    Returns:
    str/None: e.g. "1.2MB", or None for None or 0.
    """
    if not num_bytes:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024


def get_available_resolutions(info):
    """
    Lists the distinct video heights a video offers.

    Parameters:
    info (dict): yt-dlp info dict.

    Returns:
    list: Heights, highest first, e.g. [1080, 720, 480].
    """
    heights = {f["height"] for f in info.get("formats", []) if _is_video_format(f)}
    return sorted(heights, reverse=True)


def get_resolutions_with_sizes(info):
    """
    Lists each available height with its approximate download size.

    Per height, a reported size beats a bitrate x duration estimate, then the
    larger wins. Sizes are taken before the re-encode step.

    Parameters:
    info (dict): yt-dlp info dict.

    Returns:
    list: (height, size string or None) tuples, highest first.
    """
    formats = info.get("formats", [])
    duration = info.get("duration")

    def estimated_size(f):
        return int(f["tbr"] * 1000 / 8 * duration) if f.get("tbr") and duration else None

    # Audio-only formats have no height
    best_audio_size = max(
        (known_size(f) for f in formats if not f.get("height") and known_size(f)),
        default=0,
    )

    by_height = {}
    for f in formats:
        if not _is_video_format(f):
            continue
        # (1, size) for reported sizes sorts above (0, estimate)
        entry = (1, known_size(f)) if known_size(f) else (0, estimated_size(f) or 0)
        by_height.setdefault(f["height"], []).append(entry)

    results = []
    for height in sorted(by_height, reverse=True):
        _, video_size = max(by_height[height])
        total = (video_size + best_audio_size) if video_size else None
        results.append((height, format_size(total)))
    return results


def estimate_tiktok_size(info):
    """
    Estimates the size of the clip download_media("tiktok") picks.

    Parameters:
    info (dict): yt-dlp info dict for a TikTok video.

    Returns:
    str/None: Size of the largest non-watermarked format, e.g. "4.2MB".
    """
    candidates = [
        known_size(f) or 0
        for f in info.get("formats", [])
        if f.get("format_id") != TIKTOK_WATERMARK_FORMAT_ID
    ]
    return format_size(max(candidates, default=0))


def _format_opts(dtype, resolution, audio_quality="192"):
    """
    Builds the yt-dlp format options for a download type.

    Parameters:
    dtype (str): "audio", "tiktok" or "video".
    resolution (str/int/None): Max video height; None means best.
    audio_quality (str): One of AUDIO_QUALITIES; only used for audio.

    Returns:
    dict: yt-dlp options to merge into the download options.

    Raises:
    ValueError: For an audio_quality not in AUDIO_QUALITIES.
    """
    if dtype == "audio":
        if audio_quality not in AUDIO_QUALITIES:
            raise ValueError(f"Unknown audio quality: {audio_quality}")
        is_m4a = audio_quality == "m4a"
        return {
            # m4a sources convert to m4a without re-encoding
            'format': 'bestaudio[ext=m4a]/bestaudio/best' if is_m4a else 'bestaudio/best',
            'writethumbnail': False,
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a' if is_m4a else 'mp3',
                    'preferredquality': '192' if is_m4a else audio_quality,
                },
                {
                    'key': 'FFmpegMetadata',
                },
            ],
            'embedthumbnail': False,
            'prefer_ffmpeg': True,
        }
    if dtype == "tiktok":
        return {'format': f'best[format_id!={TIKTOK_WATERMARK_FORMAT_ID}]/best'}

    # Falls back to the closest lower height when the exact one doesn't exist
    height_filter = f"[height<={int(resolution)}]" if resolution else ""
    return {
        # mp4-native streams first, so Shorts don't come out as .webm
        'format': (
            f'bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/'
            f'best[ext=mp4]{height_filter}/'
            f'bestvideo{height_filter}+bestaudio/best{height_filter}/best'
        ),
        'merge_output_format': 'mp4',
    }


# ---------- Trim ----------

def parse_timestamp(text):
    """
    Converts a typed time like "90", "1:30" or "1:02:03" to seconds.

    Parameters:
    text (str/None): The time; blank means not set.

    Returns:
    float/None: Seconds, or None when blank.

    Raises:
    ValueError: If the text isn't a valid non-negative time.
    """
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not re.fullmatch(r"\d+(\.\d+)?", parts[-1]) or \
            not all(p.isdigit() for p in parts[:-1]):
        raise ValueError(f"Invalid time: {text}")
    seconds = float(parts[-1])
    # Seconds, and minutes in h:mm:ss, must be under 60
    if (len(parts) > 1 and seconds >= 60) or (len(parts) == 3 and int(parts[1]) >= 60):
        raise ValueError(f"Invalid time: {text}")
    multiplier = 60
    for part in reversed(parts[:-1]):
        seconds += int(part) * multiplier
        multiplier *= 60
    return seconds


def validate_trim(start, end, duration):
    """
    Checks a trim range against the video's length.

    Parameters:
    start (float/None): Start in seconds; None means the beginning.
    end (float/None): End in seconds; None means the end.
    duration (float/None): Video length in seconds, if known.

    Returns:
    tuple/None: (start, end) with end clamped to the duration, or None for no trim.

    Raises:
    ValueError: If end is not after start, or start is past the end of the video.
    """
    if start is None and end is None:
        return None
    if start is not None and end is not None and end <= start:
        raise ValueError("The end time must be after the start time")
    if duration:
        if start is not None and start >= duration:
            raise ValueError("The start time is past the end of the video")
        if end is not None and end > duration:
            end = duration
    return (start, end)


# ---------- Subtitles ----------

# Auto-translated captions offered for every video, on top of its own languages
TRANSLATION_LANGUAGES = ("en", "km")

_SRT_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def get_subtitle_languages(info):
    """
    Lists the subtitle languages a video offers.

    Manual tracks come first, then auto captions in the video's own language,
    then YouTube's auto-translations into TRANSLATION_LANGUAGES (it offers
    100+ translations, so the rest are left out).

    Parameters:
    info (dict): yt-dlp info dict.

    Returns:
    list: {"code", "name", "auto", "translated"} dicts, each group sorted by name.
    """
    def entries(tracks, auto, keep, translated=False):
        found = []
        for code, formats in (tracks or {}).items():
            if code == "live_chat" or not keep(code):
                continue
            name = next((f["name"] for f in formats or [] if f.get("name")), code)
            found.append({"code": code, "name": name, "auto": auto, "translated": translated})
        return sorted(found, key=lambda t: t["name"])

    language = info.get("language")

    def is_original(code):
        return code == language if language else code.endswith("-orig")

    auto_tracks = info.get("automatic_captions")
    return (entries(info.get("subtitles"), False, lambda code: True)
            + entries(auto_tracks, True, is_original)
            + entries(auto_tracks, True,
                      lambda code: code in TRANSLATION_LANGUAGES and not is_original(code),
                      translated=True))


def subtitle_path_for(video_path, lang):
    """
    Gets where yt-dlp saves a video's converted subtitles.

    Parameters:
    video_path (str): Path of the video.
    lang (str): Subtitle language code.

    Returns:
    str: e.g. "/d/Clip.km.srt" for "/d/Clip.mp4".
    """
    return f"{os.path.splitext(video_path)[0]}.{lang}.srt"


def _srt_seconds(stamp):
    """
    Converts an SRT timestamp to seconds.

    Parameters:
    stamp (str): e.g. "00:01:02,500".

    Returns:
    float: Seconds.
    """
    h, m, sec, ms = (int(x) for x in _SRT_TIME_RE.match(stamp.strip()).groups())
    return h * 3600 + m * 60 + sec + ms / 1000


def _srt_stamp(seconds):
    """
    Converts seconds to an SRT timestamp.

    Parameters:
    seconds (float): Seconds.

    Returns:
    str: e.g. "00:01:02,500".
    """
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def shift_srt(path, start, end):
    """
    Rewrites an .srt so its times line up with a trimmed clip, in place.

    Cues outside the clip are dropped; cues crossing its edges are clamped.

    Parameters:
    path (str): Path of the .srt.
    start (float/None): Clip start in the original video; None means 0.
    end (float/None): Clip end in the original video; None means the end.
    """
    start = start or 0
    with open(path, encoding="utf-8", errors="replace") as f:
        blocks = re.split(r"\n\s*\n", f.read().replace("\r\n", "\n").strip())

    kept = []
    for block in blocks:
        lines = block.split("\n")
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        first, last = lines[time_index].split("-->")
        cue_start, cue_end = _srt_seconds(first), _srt_seconds(last)
        if cue_end <= start or (end is not None and cue_start >= end):
            continue
        new_start = max(0, cue_start - start)
        new_end = cue_end - start if end is None else min(cue_end, end) - start
        text = "\n".join(lines[time_index + 1:])
        kept.append(f"{len(kept) + 1}\n{_srt_stamp(new_start)} --> {_srt_stamp(new_end)}\n{text}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept))


# ---------- Cover art (audio) ----------

def clean_title_for_search(title):
    """
    Strips bracketed junk like "[Official Video]" from a title.

    Parameters:
    title (str/None): The video title.

    Returns:
    str: The cleaned title for a music search.
    """
    cleaned = _TITLE_JUNK_RE.sub("", title or "")
    cleaned = cleaned.replace("|", " ").strip(" -|")
    return cleaned.strip()


def _normalize_for_match(s):
    """
    Normalizes text for loose matching.

    Parameters:
    s (str/None): The text.

    Returns:
    str: Lowercase letters and digits only, e.g. "Don't Stop" -> "dontstop".
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def get_itunes_cover_art(title):
    """
    Looks up official cover art on the iTunes search API.

    iTunes always returns its closest guess, so the match is only trusted
    when the track name appears in the title. Errors count as no match.

    Parameters:
    title (str): The video title.

    Returns:
    str/None: A 1200x1200 artwork URL, or None.
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

        track_name = _normalize_for_match(results[0].get("trackName"))
        if not track_name or track_name not in _normalize_for_match(query):
            return None

        artwork_url = results[0].get("artworkUrl100")
        if not artwork_url:
            return None
        # The CDN serves any size: swap 100x100 for 1200x1200
        return re.sub(r"\d+x\d+bb\.jpg$", "1200x1200bb.jpg", artwork_url)

    except Exception as e:
        print("iTunes cover art lookup failed:", e)
        return None


def make_square(image_path):
    """
    Center-crops an image to a 1920x1920 JPEG, in place.

    Parameters:
    image_path (str): Path of the image.
    """
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


def _fetch_cover_art(url, out_dir):
    """
    Downloads square cover art: iTunes artwork, else the video's largest thumbnail.

    Parameters:
    url (str): The video link.
    out_dir (str): Folder to save the image in.

    Returns:
    str/None: Path of the image, or None if none could be downloaded.
    """
    info = get_video_info(url)
    thumb_url = get_itunes_cover_art(info.get("title", ""))

    if not thumb_url:
        thumbnails = info.get("thumbnails", [])
        if thumbnails:
            thumb_url = max(thumbnails, key=lambda t: t.get("width", 0))["url"]

    if not thumb_url:
        return None

    thumb_path = os.path.join(out_dir, "thumb_temp.jpg")
    try:
        response = requests.get(thumb_url, stream=True)
        with open(thumb_path, "wb") as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        make_square(thumb_path)
        return thumb_path
    except Exception as e:
        print("Thumbnail download failed:", e)
        return None


def _embed_cover_art(audio_path, thumb_path):
    """
    Embeds an image as the mp3 or m4a's front cover, in place.

    Parameters:
    audio_path (str): Path of the mp3 or m4a.
    thumb_path (str): Path of the cover image.
    """
    tmp_path = os.path.join(os.path.dirname(audio_path), f"final_{os.path.basename(audio_path)}")
    if audio_path.lower().endswith(".m4a"):
        cover_args = ["-disposition:v:0", "attached_pic"]
    else:
        cover_args = [
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
        ]
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path, "-i", thumb_path,
            "-map", "0:0", "-map", "1:0",
            "-c:a", "copy",
            "-c:v", "mjpeg",
            *cover_args,
            tmp_path,
        ],
        capture_output=True,
    )
    _swap_in(tmp_path, audio_path, result.returncode == 0)


# ---------- ffprobe / ffmpeg ----------

def _ffprobe(path, *args):
    """
    Runs ffprobe on a file and returns its bare output values.

    Parameters:
    path (str): Path of the media file.
    *args (str): ffprobe options, e.g. "-show_entries", "format=duration".

    Returns:
    list/None: Output values in order, or None if ffprobe fails.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", *args,
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.split()
    except Exception:
        return None


def _probe_number(path, cast, *args):
    """
    Reads one number with ffprobe.

    Parameters:
    path (str): Path of the media file.
    cast (type): int or float.
    *args (str): ffprobe options.

    Returns:
    int/float/None: The first value converted with cast, or None.
    """
    try:
        return cast(_ffprobe(path, *args)[0])
    except (TypeError, IndexError, ValueError):
        return None


def probe_dimensions(path):
    """
    Reads a video's size and length.

    Parameters:
    path (str): Path of the video.

    Returns:
    tuple: (width, height, duration in seconds), or (None, None, None).
    """
    values = _ffprobe(
        path, "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
    )
    try:
        width, height, duration = values
        return int(width), int(height), int(float(duration))
    except (TypeError, ValueError):
        return None, None, None


def _get_source_fps(path):
    """
    Reads a video's frame rate.

    Parameters:
    path (str): Path of the video.

    Returns:
    str: Frame rate like "30000/1001", or "30" if unknown.
    """
    values = _ffprobe(path, "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate")
    fps = values[0] if values else None
    return fps if fps and fps != "0/0" else "30"


def _get_duration_seconds(path):
    """
    Reads a media file's duration.

    Parameters:
    path (str): Path of the media file.

    Returns:
    float/None: Duration in seconds, or None if unknown.
    """
    return _probe_number(path, float, "-show_entries", "format=duration")


def _get_height(path):
    """
    Reads a video's height.

    Parameters:
    path (str): Path of the video.

    Returns:
    int/None: Height in pixels, or None if unknown.
    """
    return _probe_number(path, int, "-select_streams", "v:0", "-show_entries", "stream=height")


def _already_social_safe(path):
    """
    Checks whether a video only needs a remux, not a re-encode.

    Parameters:
    path (str): Path of the video.

    Returns:
    bool: True if it is already constant-frame-rate H.264 yuv420p.
    """
    values = _ffprobe(
        path, "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt,r_frame_rate,avg_frame_rate",
    )
    if not values or len(values) != 4:
        return False
    codec, pix_fmt, r_fps, avg_fps = values
    return codec == "h264" and pix_fmt == "yuv420p" and r_fps == avg_fps


def _remove_if_exists(path):
    """
    Deletes a file if it exists.

    Parameters:
    path (str): Path of the file.
    """
    if os.path.exists(path):
        os.remove(path)


def _swap_in(tmp_path, path, ok):
    """
    Replaces a file with its processed copy, or discards the copy.

    Parameters:
    tmp_path (str): Path of the processed copy.
    path (str): Path of the original file.
    ok (bool): Whether processing succeeded.

    Returns:
    bool: True if the original was replaced.
    """
    if ok and os.path.exists(tmp_path):
        os.replace(tmp_path, path)
        return True
    _remove_if_exists(tmp_path)
    return False


def normalize_for_social(path, progress_hook=None, max_height=None, burn_subtitles=None):
    """
    Makes a video safe to post on Instagram/TikTok/Facebook, in place.

    Their upload pipelines choke on variable frame rate (choppy playback) and
    on AV1/HEVC (slow uploads), so the video is re-encoded to constant-frame-rate
    H.264/AAC mp4 at CRF 18. Videos that already qualify are only remuxed
    with +faststart, losslessly. On any ffmpeg failure the original is kept.

    Parameters:
    path (str): Path of the video.
    progress_hook (callable/None): Receives {"status": "normalizing", "percent": int}.
    max_height (str/int/None): Downscales taller videos, forcing a re-encode.
    burn_subtitles (str/None): Path of an .srt to draw onto the picture, forcing a re-encode.

    Returns:
    bool: False if a needed re-encode (or the subtitle burn) failed and the
    original was kept; True otherwise.
    """
    def report(percent):
        if progress_hook:
            progress_hook({"status": "normalizing", "percent": percent})

    needs_downscale = False
    if max_height:
        height = _get_height(path)
        needs_downscale = height is not None and height > int(max_height)

    if _already_social_safe(path) and not needs_downscale and not burn_subtitles:
        tmp_path = path + ".faststart.mp4"
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-c", "copy", "-movflags", "+faststart", tmp_path],
                capture_output=True, timeout=120,
            )
            _swap_in(tmp_path, path, result.returncode == 0)
        except Exception as e:
            print("Fast-start remux failed, keeping original file:", e)
            _remove_if_exists(tmp_path)
        report(100)
        # The original was already social-safe, so a failed remux loses nothing
        return True

    path = os.path.abspath(path)
    fps = _get_source_fps(path)
    duration = _get_duration_seconds(path)
    tmp_path = path + ".normalized.mp4"

    filters = [f"scale=-2:'min(ih,{int(max_height)})'"] if needs_downscale else []
    burn_copy = None
    if burn_subtitles:
        # The ass filter (unlike subtitles) can force complex shaping, without
        # which Khmer vowel signs render as loose glyphs. A plain file name in
        # ffmpeg's working folder needs no filter escaping.
        burn_copy = os.path.join(os.path.dirname(path), "burn_subs.ass")
        filters.append("ass=burn_subs.ass:shaping=complex")
    scale_args = ["-vf", ",".join(filters)] if filters else []

    try:
        if burn_copy:
            converted = subprocess.run(["ffmpeg", "-y", "-i", burn_subtitles, burn_copy],
                                       capture_output=True, timeout=60)
            if converted.returncode != 0 or not os.path.exists(burn_copy):
                print("Subtitle conversion failed:", (converted.stderr or b"")[-500:])
                return False

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
            cwd=os.path.dirname(path),
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
                    report(percent)

        _, stderr = process.communicate(timeout=600)

        if _swap_in(tmp_path, path, process.returncode == 0):
            report(100)
            return True
        print(
            "Social-media normalization failed, keeping original file:",
            (stderr or "")[-500:],
        )
        return False
    except Exception as e:
        print("Social-media normalization failed, keeping original file:", e)
        _remove_if_exists(tmp_path)
        return False
    finally:
        if burn_copy:
            _remove_if_exists(burn_copy)


# ---------- Download ----------

def download_media(url, dtype, out_dir, progress_hook=None, resolution=None,
                   audio_quality="192", trim=None, subtitles=None):
    """
    Downloads a video or its audio.

    Parameters:
    url (str): The video link.
    dtype (str): "video" (mp4), "audio" (mp3 with cover art) or "tiktok" (no watermark).
    out_dir (str): Folder to save the file in.
    progress_hook (callable/None): Receives yt-dlp progress dicts, then "normalizing" updates.
    resolution (str/int/None): Max height for video and TikTok; None means best.
    audio_quality (str): One of AUDIO_QUALITIES, for audio downloads.
    trim (tuple/None): (start, end) seconds from validate_trim(); either may be None.
    subtitles (dict/None): {"lang": code, "mode": "srt" or "burn"}; ignored for audio.
        The .srt (mode "srt") is saved at subtitle_path_for(returned path, lang).

    Returns:
    str: Path of the downloaded file.

    Raises:
    yt_dlp.utils.DownloadError: If the download fails.
    ValueError: For TikTok photo posts, which yt-dlp can't download, or
        when burn-in subtitles were asked for but none exist in that
        language or burning them failed.
    """
    if is_tiktok(url) and "/photo/" in url.lower():
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
        **_format_opts(dtype, resolution, audio_quality),
    }
    if trim:
        start, end = trim
        # Only the chosen part is downloaded; cuts snap to re-encoded keyframes
        ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(
            None, [(start or 0, end if end is not None else float("inf"))]
        )
        ydl_opts['force_keyframes_at_cuts'] = True
    if dtype == "audio":
        subtitles = None
    if subtitles:
        ydl_opts.update(
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=[subtitles["lang"]],
        )
        ydl_opts.setdefault('postprocessors', []).append(
            {'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'}
        )
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    thumb_path = _fetch_cover_art(url, out_dir) if dtype == "audio" else None

    info, effective_opts = extract_info_with_fallback(ydl_opts, url, download=True)

    # prepare_filename() applies the same truncation/sanitizing as the outtmpl
    with yt_dlp.YoutubeDL(effective_opts) as ydl:
        filename = os.path.basename(ydl.prepare_filename(info))
    if dtype == "audio":
        filename = os.path.splitext(filename)[0] + (".m4a" if audio_quality == "m4a" else ".mp3")
    final_path = os.path.join(out_dir, filename)

    if thumb_path and os.path.exists(final_path):
        _embed_cover_art(final_path, thumb_path)
    if thumb_path:
        _remove_if_exists(thumb_path)

    srt_path = subtitle_path_for(final_path, subtitles["lang"]) if subtitles else None
    if srt_path and trim and os.path.exists(srt_path):
        # Subtitles always cover the whole video; move them to the clip's timeline
        shift_srt(srt_path, *trim)
    burn_path = None
    if subtitles and subtitles["mode"] == "burn":
        if not os.path.exists(srt_path):
            raise ValueError("No subtitles found for that language")
        burn_path = srt_path

    if dtype in ("video", "tiktok") and os.path.exists(final_path):
        # Also caps TikTok, whose format string has no height filter
        normalized = normalize_for_social(final_path, progress_hook=progress_hook,
                                          max_height=resolution, burn_subtitles=burn_path)
        if burn_path and not normalized:
            _remove_if_exists(burn_path)
            raise ValueError("Couldn't burn the subtitles into the video")
    if burn_path:
        _remove_if_exists(burn_path)

    return final_path
