"""
Telegram bot front-end for the same download logic the web app uses
(downloader.py) - paste a link, pick Fast or Full quality, get the
video back.

Auto-detects what to send:
  - a tiktok.com link -> downloads the watermark-free version
  - anything else -> downloads as video

Run:
    python telegram_bot.py

Requires TELEGRAM_BOT_TOKEN in a .env file next to this script (see
.env.example).
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
import uuid

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from downloader import (
    download_media,
    download_percent,
    format_size,
    get_resolutions_with_sizes,
    get_video_info,
)

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# The height "Fast" downloads are capped to. A smaller file uploads
# proportionally faster on a slow/throttled connection, at the cost of
# resolution - offered as a per-link choice (see the inline buttons in
# handle_message) rather than a fixed setting, since which trade-off is
# worth it depends on the video and the connection at the time.
FAST_QUALITY_MAX_HEIGHT = os.environ.get("FAST_MODE_MAX_HEIGHT") or "480"

# Holds a not-yet-answered link's (url, dtype) between handle_message
# showing the Fast/Full buttons and handle_quality_choice acting on
# whichever one gets pressed - keyed by a short id because Telegram
# caps callback_data at 64 bytes, too small to fit a real URL in.
# In-memory only: entries for buttons nobody ever presses just sit here
# harmlessly for the life of the process (fine for a personal bot; a
# high-traffic one would want these to expire).
PENDING_LINKS = {}

# Telegram's Bot API rejects uploads over 50MB (regardless of your own
# server/connection) - there is no way around this without running a
# self-hosted Bot API server (2GB limit), which is out of scope here.
MAX_TELEGRAM_UPLOAD_BYTES = 50 * 1024 * 1024

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("video_downloader_bot")


def probe_video_dimensions(path: str):
    """
    Returns (width, height, duration_seconds) for the video at `path`,
    or (None, None, None) if ffprobe can't tell.

    Telegram's own player sizes/crops the video's on-screen box using
    whatever width/height/duration the *bot* declares in sendVideo -
    not by inspecting the file itself first. Leaving these out (as
    before) makes Telegram guess a box, which for portrait (9:16)
    clips renders visibly stretched/squished even though the file
    itself is perfectly fine - this is what actually produces the
    "wrong ratio" you see after downloading via the bot.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        width, height, duration = result.stdout.split()
        return int(width), int(height), int(float(duration))
    except Exception:
        return None, None, None


def pick_dtype(url: str) -> str:
    """TikTok links get the watermark-free path; everything else is a
    plain video download (audio-only isn't offered here - add a /audio
    command later if that's wanted). This covers Instagram, Facebook,
    and X/Twitter links too, though IG/FB only actually succeed if a
    real cookies.txt is set up (see downloader.py's COOKIE_FILE) - both
    sites refuse logged-out requests almost entirely, unlike TikTok's
    and X's public links."""
    return "tiktok" if "tiktok.com" in url.lower() else "video"


def is_youtube(url: str) -> bool:
    """True for youtube.com/youtu.be links - these get the full
    per-video resolution picker (handle_message) instead of the
    Low/HQ choice every other site gets, since YouTube is the one site
    here that reliably exposes a real resolution ladder (144p-4K) to
    choose from up front."""
    url = url.lower()
    return "youtube.com" in url or "youtu.be" in url


PROGRESS_BAR_LEN = 12
# Telegram throttles how often a bot can edit the *same* message - editing
# on every single yt-dlp progress tick (many times a second) trips that
# and starts silently dropping updates. Editing at most twice a second
# stays well under the limit while still feeling live rather than static.
MIN_EDIT_INTERVAL = 0.5


def render_bar(percent: int) -> str:
    filled = int(PROGRESS_BAR_LEN * percent / 100)
    return "▓" * filled + "░" * (PROGRESS_BAR_LEN - filled)


def make_progress_hook(status_msg, loop: asyncio.AbstractEventLoop):
    """
    Builds a yt-dlp/downloader progress_hook that keeps editing
    `status_msg` in place with a live animated bar, instead of the
    previous single static "⏳ Downloading..." message for the whole
    download+re-encode. download_media() runs this hook from a worker
    thread (see handle_message's asyncio.to_thread call below), so
    edits are handed back to the bot's event loop via
    run_coroutine_threadsafe rather than awaited directly here.
    """
    state = {"last_text": None, "last_edit": 0.0}

    def schedule_edit(text: str):
        now = time.monotonic()
        if text == state["last_text"] or now - state["last_edit"] < MIN_EDIT_INTERVAL:
            return
        state["last_text"] = text
        state["last_edit"] = now

        async def do_edit():
            try:
                await status_msg.edit_text(text)
            except BadRequest:
                pass  # e.g. "message is not modified" - harmless, ignore

        asyncio.run_coroutine_threadsafe(do_edit(), loop)

    def hook(d):
        status = d.get("status")
        # One continuous bar across both phases (download, then the
        # post-download CFR re-encode - see downloader.py) instead of
        # two separate 0-100% bars back to back, which read as the job
        # finishing and restarting rather than one job progressing.
        # Download fills the first half, optimize fills the second.
        if status == "downloading":
            percent = download_percent(d)
            if percent is not None:
                schedule_edit(f"⬇️ Downloading  {render_bar(percent // 2)}  {percent // 2}%")
        elif status == "finished":
            schedule_edit(f"⚙️ Optimizing  {render_bar(50)}  50%")
        elif status == "normalizing":
            percent = 50 + d.get("percent", 0) // 2
            schedule_edit(f"⚙️ Optimizing  {render_bar(percent)}  {percent}%")

    return hook


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if not text.lower().startswith(("http://", "https://")):
        await update.message.reply_text(
            "Send me a YouTube or TikTok link and I'll send the video back "
            "(TikTok links come back with the watermark removed)."
        )
        return

    dtype = pick_dtype(text)
    request_id = uuid.uuid4().hex[:10]
    PENDING_LINKS[request_id] = (text, dtype)

    if dtype == "video" and is_youtube(text):
        await show_resolution_picker(update.message, text, request_id)
        return

    # A quick placeholder first, same as the YouTube resolution picker -
    # _estimate_low_hq_sizes() below makes a real network request, so
    # showing nothing until it resolves would leave the chat looking
    # unresponsive for that stretch.
    status_msg = await update.message.reply_text("🎬 Checking file size...")
    low_size, hq_size = await _estimate_low_hq_sizes(text, dtype)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🔽 Low ({low_size})" if low_size else "🔽 Low",
            callback_data=f"q:fast:{request_id}",
        ),
        InlineKeyboardButton(
            f"🔼 HIGH ({hq_size})" if hq_size else "🔼 HIGH",
            callback_data=f"q:full:{request_id}",
        ),
    ]])
    # Telegram always attaches inline buttons to a real message bubble -
    # there's no way to have buttons with no bubble at all, so it gets a
    # short, real label instead of trying to hide it (an empty/invisible
    # one still renders as a bubble, just a blank-looking one).
    await status_msg.edit_text("Choose the option", reply_markup=keyboard)


async def _estimate_low_hq_sizes(url: str, dtype: str):
    """
    Returns (low_size_str, hq_size_str), either of which is None if it
    couldn't be determined - the caller falls back to a plain "Low"/"HQ"
    label with no size in that case rather than blocking the download
    over what's just a nice-to-have.

    For dtype == "video" (X/Twitter, IG, FB): "HQ" is the largest
    available height's size, "Low" is the size at (or just under)
    FAST_QUALITY_MAX_HEIGHT, or the smallest available height if the
    video has nothing that low - these genuinely differ, since
    download_media does apply that height cap for "video".

    For dtype == "tiktok": both are the *same* single size, deliberately
    - TikTok's own format selection ('best[format_id!=download]/best')
    has no height filter to cap in the first place (see download_media),
    so Low and HQ already produce the identical downloaded file for
    TikTok. Showing two different numbers there would be actively
    misleading, not just imprecise.
    """
    try:
        info = await asyncio.to_thread(get_video_info, url)
    except Exception:
        return None, None

    if dtype == "tiktok":
        size = _estimate_tiktok_size(info)
        return size, size

    resolutions = get_resolutions_with_sizes(info)  # highest height first
    if not resolutions:
        return None, None

    hq_size = resolutions[0][1]
    cap = int(FAST_QUALITY_MAX_HEIGHT)
    low_size = next((size for h, size in resolutions if h <= cap), resolutions[-1][1])
    return low_size, hq_size


def _estimate_tiktok_size(info):
    """
    Best-effort size of whatever download_media's TikTok format string
    ('best[format_id!=download]/best') would actually pick: the
    non-watermarked format ("download" is always TikTok's watermarked
    stream's format_id - see download_media's own comment on this)
    with the largest known size, since yt-dlp's "best" heuristic
    generally tracks bitrate/quality too.
    """
    candidates = [
        (f.get("filesize") or f.get("filesize_approx") or 0)
        for f in info.get("formats", [])
        if f.get("format_id") != "download"
    ]
    return format_size(max(candidates, default=0))


async def show_resolution_picker(reply_to_message, url: str, request_id: str) -> None:
    """
    YouTube-only: replaces the usual Low/HQ choice with a button for
    every resolution the video actually has (144p up to whatever its
    real ceiling is, e.g. 4K) - YouTube is the one site here that
    reliably exposes a full resolution ladder up front, unlike TikTok/
    X/IG/FB, so it's worth letting the user pick precisely instead of
    just a coarse low/high split.
    """
    status_msg = await reply_to_message.reply_text("🎬 Checking available resolutions...")

    try:
        # get_video_info() makes a real network request (metadata-only,
        # no download) - run off the event loop so it can't block other
        # chats, same reasoning as the download itself further down.
        info = await asyncio.to_thread(get_video_info, url)
        resolutions = get_resolutions_with_sizes(info)
    except Exception as e:
        logger.exception("Failed to fetch resolutions for %s", url)
        PENDING_LINKS.pop(request_id, None)
        await status_msg.edit_text(f"❌ Couldn't check this video: {e}")
        return

    # resolutions is [] when the site/extractor exposes no height info
    # at all (rare) - the loop below then contributes no buttons, and
    # the "Best available" button added after it is still there as the
    # only option. Sizes are approximate (see get_resolutions_with_sizes'
    # own docstring) and missing entirely for some obscure formats, in
    # which case the button just falls back to showing the height alone.
    buttons = [
        InlineKeyboardButton(
            f"{h}p ({size})" if size else f"{h}p",
            callback_data=f"r:{h}:{request_id}",
        )
        for h, size in resolutions
    ]
    buttons.append(InlineKeyboardButton("⭐ Best available", callback_data=f"r:best:{request_id}"))
    # 3 per row keeps a full 144p-4K ladder (8 heights + Best = 9
    # buttons) to a compact 3x3 grid instead of one long column.
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]

    note = (
        "⚠️ Only low quality is available for this video right now "
        "(an active YouTube restriction, not something this bot controls).\n\n"
        if len(resolutions) <= 1
        else ""
    )
    await status_msg.edit_text(
        f"{note}Choose the option", reply_markup=InlineKeyboardMarkup(rows)
    )


async def handle_resolution_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, height, request_id = query.data.split(":", 2)
    entry = PENDING_LINKS.pop(request_id, None)
    if entry is None:
        await query.edit_message_text("This request has expired - send the link again.")
        return
    text, dtype = entry
    resolution = None if height == "best" else height

    await run_download_and_send(update, context, query.message, text, dtype, resolution)


async def handle_quality_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # stops Telegram's client-side loading spinner on the button

    _, quality, request_id = query.data.split(":", 2)
    entry = PENDING_LINKS.pop(request_id, None)
    if entry is None:
        # Bot restarted (PENDING_LINKS is in-memory only) or this button
        # was already pressed once - either way there's nothing left to
        # act on.
        await query.edit_message_text("This request has expired - send the link again.")
        return
    text, dtype = entry
    resolution = FAST_QUALITY_MAX_HEIGHT if quality == "fast" else None

    await run_download_and_send(update, context, query.message, text, dtype, resolution)


async def run_download_and_send(update, context, status_msg, text, dtype, resolution) -> None:
    """Shared by both the Low/HQ flow (handle_quality_choice) and the
    YouTube resolution picker (handle_resolution_choice) - everything
    after "a quality/resolution has been chosen" is identical either
    way, just with a different `resolution` value."""
    # reply_markup=None explicitly clears whichever buttons led here -
    # editing a message's text alone leaves its existing inline keyboard
    # attached, so without this they'd stay visible (and clickable,
    # pointlessly) through the whole download.
    await status_msg.edit_text(f"⬇️ Downloading  {render_bar(0)}  0%", reply_markup=None)

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO
            )
            loop = asyncio.get_running_loop()
            hook = make_progress_hook(status_msg, loop)
            # download_media() is synchronous and blocking (yt-dlp +
            # ffmpeg) - running it directly here would freeze the bot
            # for every other chat until it finished. to_thread() keeps
            # the event loop free so the progress edits above (and other
            # users' messages) still get through while this runs.
            final_path = await asyncio.to_thread(
                download_media, text, dtype, tmp_dir,
                progress_hook=hook, resolution=resolution,
            )
        except Exception as e:
            logger.exception("Download failed for %s", text)
            await status_msg.edit_text(f"❌ Download failed: {e}")
            return

        size = os.path.getsize(final_path)
        if size > MAX_TELEGRAM_UPLOAD_BYTES:
            await status_msg.edit_text(
                f"❌ That file is {size / 1024 / 1024:.1f}MB - Telegram bots "
                f"can't send files over 50MB. Try a shorter clip."
            )
            return

        # Deliberately no "Uploading to Telegram..." status here - the
        # last download/optimize progress message just stays on screen
        # (get deleted below once the video itself lands) instead of
        # switching to a separate "uploading" message beforehand.
        width, height, duration = probe_video_dimensions(final_path)

        # send_video (the actual multipart upload) used to have no error
        # handling at all: a timeout here - which happens, since it's a
        # real upload over the network rather than a small JSON request -
        # raised out of this function uncaught, leaving status_msg stuck
        # on "Uploading..." forever with no error shown and the bot
        # silently dropping the whole request. Retry once (a fresh
        # attempt, not a resumed one - Telegram's API doesn't support
        # resuming a partial upload) before actually giving up and
        # telling the user, since a single timeout is often just a
        # transient network hiccup rather than a real failure.
        last_error = None
        for attempt in range(1, 3):
            try:
                with open(final_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f,
                        caption=os.path.splitext(os.path.basename(final_path))[0][:1024],
                        supports_streaming=True,
                        width=width,
                        height=height,
                        duration=duration,
                        # 600s (not 120s) because this dev sandbox's own
                        # outbound bandwidth is throttled to ~24KB/s
                        # (confirmed directly with curl, independent of
                        # this code) - a real deployment on normal
                        # bandwidth won't need anywhere near this long,
                        # but it lets uploads actually complete while
                        # testing from here instead of timing out on
                        # every attempt.
                        write_timeout=600,
                        read_timeout=600,
                    )
                last_error = None
                break
            except (TimedOut, NetworkError) as e:
                last_error = e
                logger.warning("send_video attempt %d failed for %s: %s", attempt, text, e)
                if attempt < 2:
                    await status_msg.edit_text("📤 Upload timed out, retrying...")

        if last_error is not None:
            logger.error("send_video failed for %s", text, exc_info=last_error)
            await status_msg.edit_text(
                "❌ Upload to Telegram timed out after retrying. This is usually a "
                "transient network issue, not the video itself - try again."
            )
            return

        await status_msg.delete()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
            "paste your BotFather token in there."
        )

    # python-telegram-bot processes updates one at a time by default
    # (concurrent_updates off) - a single long download/normalize job
    # (this bot's whole point) would otherwise block every other
    # incoming message, including from other chats, until it finishes,
    # which looks exactly like the bot being dead. concurrent_updates
    # lets independent messages be handled in parallel instead.
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_quality_choice, pattern=r"^q:"))
    application.add_handler(CallbackQueryHandler(handle_resolution_choice, pattern=r"^r:"))

    logger.info("Bot starting (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
