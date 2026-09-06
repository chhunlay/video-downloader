"""
Telegram bot front-end for the same download logic the web app uses
(downloader.py) - reply-back-with-the-file, no buttons: paste a link,
get the file back.

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

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from downloader import download_media, download_percent

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

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
    command later if that's wanted). This covers Instagram and Facebook
    links too, though those only actually succeed if a real cookies.txt
    is set up (see downloader.py's COOKIE_FILE) - both sites refuse
    logged-out requests almost entirely, unlike TikTok's public links."""
    return "tiktok" if "tiktok.com" in url.lower() else "video"


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
        if status == "downloading":
            percent = download_percent(d)
            if percent is not None:
                schedule_edit(f"⬇️ Downloading  {render_bar(percent)}  {percent}%")
        elif status == "finished":
            schedule_edit("⚙️ Processing…")
        elif status == "normalizing":
            # The post-download CFR re-encode (see downloader.py) - can
            # take longer than the download on a longer video, so it
            # gets its own bar instead of the bot looking frozen.
            percent = d.get("percent", 0)
            schedule_edit(f"⚙️ Optimizing for social media  {render_bar(percent)}  {percent}%")

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
    status_msg = await update.message.reply_text(f"⬇️ Downloading  {render_bar(0)}  0%")

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
                download_media, text, dtype, tmp_dir, progress_hook=hook
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

        await status_msg.edit_text("📤 Uploading to Telegram...")
        width, height, duration = probe_video_dimensions(final_path)
        with open(final_path, "rb") as f:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=f,
                caption=os.path.splitext(os.path.basename(final_path))[0][:1024],
                supports_streaming=True,
                width=width,
                height=height,
                duration=duration,
                write_timeout=120,
                read_timeout=120,
            )

        await status_msg.delete()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
            "paste your BotFather token in there."
        )

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
