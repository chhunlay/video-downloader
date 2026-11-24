"""Download counts for the page footer: one JSON line per finished download, kept for good."""

import json
import os
import sys
import threading
import time
from collections import Counter

from downloader import site_from_url

# Shared by the web app and the Telegram bot; outside the 24-hour download folder
_DATA_ROOT = (os.path.expanduser("~/Library/Application Support") if sys.platform == "darwin"
              else os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"))
STATS_FILE = os.path.join(_DATA_ROOT, "Video Downloader", "download-stats.jsonl")

WEEK_SECONDS = 7 * 24 * 3600

_lock = threading.Lock()


def record(path, dtype, site, size, now=None):
    """
    Saves one finished download. Only the time, type, site and size are kept, never the link or title.

    Parameters:
    path (str): The stats file (created with its folder if missing).
    dtype (str): "video", "audio" or "tiktok".
    site (str/None): The site's name, e.g. "YouTube".
    size (int): The file size in bytes.
    now (float/None): The time to save; defaults to now.
    """
    line = {"time": int(now if now is not None else time.time()), "type": dtype, "site": site, "bytes": size}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def count_download(path, url, dtype, size):
    """
    Saves one finished download from its link; a stats error is printed, never raised.

    Parameters:
    path (str): The stats file.
    url (str): The video link (only its site is saved).
    dtype (str): "video", "audio" or "tiktok".
    size (int): The file size in bytes.
    """
    try:
        record(path, dtype, site_from_url(url), size)
    except Exception as e:
        print("Could not save download stats:", e)


def _read(path):
    """
    Reads the saved downloads, skipping broken lines.

    Parameters:
    path (str): The stats file.

    Returns:
    list: One dict per download; empty when the file doesn't exist yet.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("time"), (int, float)):
            entries.append(entry)
    return entries


def summary(path, now=None):
    """
    Adds up the saved downloads for the page.

    TikTok downloads count as video. Percentages are rounded and add up to 100.

    Parameters:
    path (str): The stats file.
    now (float/None): The current time; defaults to now.

    Returns:
    dict: {"total", "this_week", "video_percent", "audio_percent", "top_site",
    "total_bytes"}; top_site is None when nothing was downloaded.
    """
    now = now if now is not None else time.time()
    entries = _read(path)
    total = len(entries)

    # Split by type and find the most used site
    audio = sum(1 for e in entries if e.get("type") == "audio")
    audio_percent = round(audio * 100 / total) if total else 0
    sites = Counter(e.get("site") for e in entries if e.get("site"))

    return {
        "total": total,
        "this_week": sum(1 for e in entries if now - e["time"] < WEEK_SECONDS),
        "video_percent": 100 - audio_percent if total else 0,
        "audio_percent": audio_percent,
        "top_site": sites.most_common(1)[0][0] if sites else None,
        "total_bytes": sum(e["bytes"] for e in entries if isinstance(e.get("bytes"), int)),
    }
