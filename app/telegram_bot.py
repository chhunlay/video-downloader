"""
Telegram bot front-end for downloader.py: send a link, pick a quality, get the video back.

TikTok links download without the watermark; everything else downloads as video.

Run: python telegram_bot.py  (needs TELEGRAM_BOT_TOKEN in .env, see .env.example)
"""

import asyncio
import logging
import os
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
    estimate_tiktok_size,
    get_resolutions_with_sizes,
    get_video_info,
    is_tiktok,
    probe_dimensions,
)

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Max height for the "Low" option (smaller file, faster upload)
FAST_QUALITY_MAX_HEIGHT = os.environ.get("FAST_MODE_MAX_HEIGHT") or "480"

# request_id -> (url, dtype) until a quality button is pressed.
# Needed because callback_data is capped at 64 bytes; in-memory only.
PENDING_LINKS = {}

# Bot API upload limit
MAX_TELEGRAM_UPLOAD_BYTES = 50 * 1024 * 1024

UPLOAD_ATTEMPTS = 2

PROGRESS_BAR_LEN = 12
# Telegram drops edits to the same message if they come too fast
MIN_EDIT_INTERVAL = 0.5

DOWNLOADING = "⬇️ Downloading"
OPTIMIZING = "⚙️ Optimizing"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("video_downloader_bot")


def pick_dtype(url: str) -> str:
    """
    Chooses the download type for a link.

    Login-only links (Vimeo, Reddit, private Instagram/Facebook posts) need a real cookies.txt.

    Parameters:
    url (str): The link.

    Returns:
    str: "tiktok" for TikTok links, otherwise "video".
    """
    return "tiktok" if is_tiktok(url) else "video"


def is_youtube(url: str) -> bool:
    """
    Checks whether a link points to YouTube.

    Parameters:
    url (str): The link.

    Returns:
    bool: True for youtube.com and youtu.be links.
    """
    url = url.lower()
    return "youtube.com" in url or "youtu.be" in url


def with_size(label: str, size) -> str:
    """
    Adds a file size to a button label.

    Parameters:
    label (str): The label, e.g. "720p".
    size (str/None): The size, e.g. "12.3MB".

    Returns:
    str: "720p (12.3MB)", or just the label when size is None.
    """
    return f"{label} ({size})" if size else label


def render_bar(percent: int) -> str:
    """
    Draws a text progress bar.

    Parameters:
    percent (int): 0-100.

    Returns:
    str: The bar, e.g. "▓▓▓░░░░░░░░░".
    """
    filled = int(PROGRESS_BAR_LEN * percent / 100)
    return "▓" * filled + "░" * (PROGRESS_BAR_LEN - filled)


def progress_text(label: str, percent: int) -> str:
    """
    Builds a progress message.

    Parameters:
    label (str): DOWNLOADING or OPTIMIZING.
    percent (int): 0-100.

    Returns:
    str: e.g. "⬇️ Downloading  ▓▓░░░░░░░░░░  20%".
    """
    return f"{label}  {render_bar(percent)}  {percent}%"


def make_progress_hook(status_msg, loop: asyncio.AbstractEventLoop):
    """
    Builds a download_media progress hook that edits a message with a live bar.

    The hook runs in a worker thread, so edits are scheduled on the bot's loop.
    Download fills 0-50% and the re-encode fills 50-100%, as one bar.

    Parameters:
    status_msg (telegram.Message): The message to edit.
    loop (asyncio.AbstractEventLoop): The bot's event loop.

    Returns:
    callable: The progress hook.
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
                pass  # e.g. "message is not modified"

        asyncio.run_coroutine_threadsafe(do_edit(), loop)

    def hook(d):
        status = d.get("status")
        if status == "downloading":
            percent = download_percent(d)
            if percent is not None:
                schedule_edit(progress_text(DOWNLOADING, percent // 2))
        elif status == "finished":
            schedule_edit(progress_text(OPTIMIZING, 50))
        elif status == "normalizing":
            schedule_edit(progress_text(OPTIMIZING, 50 + d.get("percent", 0) // 2))

    return hook


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Replies to a link with quality buttons (a resolution picker for YouTube).

    Parameters:
    update (Update): The incoming message.
    context (ContextTypes.DEFAULT_TYPE): The bot context.
    """
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

    status_msg = await update.message.reply_text("🎬 Checking file size...")
    low_size, hq_size = await _estimate_low_hq_sizes(text, dtype)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(with_size("🔽 Low", low_size), callback_data=f"q:fast:{request_id}"),
        InlineKeyboardButton(with_size("🔼 HIGH", hq_size), callback_data=f"q:full:{request_id}"),
    ]])
    await status_msg.edit_text("Choose the option", reply_markup=keyboard)


async def _estimate_low_hq_sizes(url: str, dtype: str):
    """
    Estimates the file sizes for the Low and HIGH buttons.

    TikTok has no height cap, so Low and HIGH are the same file and size.

    Parameters:
    url (str): The video link.
    dtype (str): "video" or "tiktok".

    Returns:
    tuple: (low size, high size) strings; either may be None.
    """
    try:
        info = await asyncio.to_thread(get_video_info, url)
    except Exception:
        return None, None

    if dtype == "tiktok":
        size = estimate_tiktok_size(info)
        return size, size

    resolutions = get_resolutions_with_sizes(info)  # highest first
    if not resolutions:
        return None, None

    hq_size = resolutions[0][1]
    cap = int(FAST_QUALITY_MAX_HEIGHT)
    low_size = next((size for h, size in resolutions if h <= cap), resolutions[-1][1])
    return low_size, hq_size


async def show_resolution_picker(reply_to_message, url: str, request_id: str) -> None:
    """
    Shows one button per available resolution, plus "Best available" (YouTube only).

    Parameters:
    reply_to_message (telegram.Message): The message to reply to.
    url (str): The video link.
    request_id (str): Key of the link in PENDING_LINKS.
    """
    status_msg = await reply_to_message.reply_text("🎬 Checking available resolutions...")

    try:
        info = await asyncio.to_thread(get_video_info, url)
        resolutions = get_resolutions_with_sizes(info)
    except Exception as e:
        logger.exception("Failed to fetch resolutions for %s", url)
        PENDING_LINKS.pop(request_id, None)
        await status_msg.edit_text(f"❌ Couldn't check this video: {e}")
        return

    buttons = [
        InlineKeyboardButton(with_size(f"{h}p", size), callback_data=f"r:{h}:{request_id}")
        for h, size in resolutions
    ]
    buttons.append(InlineKeyboardButton("⭐ Best available", callback_data=f"r:best:{request_id}"))
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


async def _take_pending(query):
    """
    Answers a button press and takes its link out of PENDING_LINKS.

    Parameters:
    query (telegram.CallbackQuery): The button press.

    Returns:
    tuple/None: (url, dtype, choice), or None if the link expired.
    """
    await query.answer()
    _, choice, request_id = query.data.split(":", 2)
    entry = PENDING_LINKS.pop(request_id, None)
    if entry is None:
        # Bot restarted, or the button was already pressed
        await query.edit_message_text("This request has expired - send the link again.")
        return None
    return (*entry, choice)


async def handle_resolution_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Downloads at the resolution picked from the YouTube picker.

    Parameters:
    update (Update): The button press.
    context (ContextTypes.DEFAULT_TYPE): The bot context.
    """
    query = update.callback_query
    pending = await _take_pending(query)
    if pending:
        text, dtype, height = pending
        resolution = None if height == "best" else height
        await run_download_and_send(update, context, query.message, text, dtype, resolution)


async def handle_quality_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Downloads at the picked quality: Low (capped height) or HIGH (best).

    Parameters:
    update (Update): The button press.
    context (ContextTypes.DEFAULT_TYPE): The bot context.
    """
    query = update.callback_query
    pending = await _take_pending(query)
    if pending:
        text, dtype, quality = pending
        resolution = FAST_QUALITY_MAX_HEIGHT if quality == "fast" else None
        await run_download_and_send(update, context, query.message, text, dtype, resolution)


async def run_download_and_send(update, context, status_msg, text, dtype, resolution) -> None:
    """
    Downloads the video and uploads it to the chat.

    Parameters:
    update (Update): The update that triggered the download.
    context (ContextTypes.DEFAULT_TYPE): The bot context.
    status_msg (telegram.Message): The message used for progress.
    text (str): The video link.
    dtype (str): "video" or "tiktok".
    resolution (str/None): Max height; None means best.
    """
    # reply_markup=None removes the choice buttons
    await status_msg.edit_text(progress_text(DOWNLOADING, 0), reply_markup=None)
    chat_id = update.effective_chat.id

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
            hook = make_progress_hook(status_msg, asyncio.get_running_loop())
            # Blocking yt-dlp/ffmpeg work runs in a thread to keep the bot responsive
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

        # Telegram sizes the player from these, not from the file
        width, height, duration = probe_dimensions(final_path)

        # Upload timeouts are often transient, so retry once with a fresh upload
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            try:
                with open(final_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=os.path.splitext(os.path.basename(final_path))[0][:1024],
                        supports_streaming=True,
                        width=width,
                        height=height,
                        duration=duration,
                        # Long timeouts so large files finish on slow connections
                        write_timeout=600,
                        read_timeout=600,
                    )
                break
            except (TimedOut, NetworkError) as e:
                logger.warning("send_video attempt %d failed for %s: %s", attempt, text, e)
                if attempt < UPLOAD_ATTEMPTS:
                    await status_msg.edit_text("📤 Upload timed out, retrying...")
                    continue
                logger.error("send_video failed for %s", text, exc_info=e)
                await status_msg.edit_text(
                    "❌ Upload to Telegram timed out after retrying. This is usually a "
                    "transient network issue, not the video itself - try again."
                )
                return

        await status_msg.delete()


def main() -> None:
    """Starts the bot with long polling."""
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
            "paste your BotFather token in there."
        )

    # concurrent_updates: a long download must not block other chats
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_quality_choice, pattern=r"^q:"))
    application.add_handler(CallbackQueryHandler(handle_resolution_choice, pattern=r"^r:"))

    logger.info("Bot starting (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
