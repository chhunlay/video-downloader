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

import logging
import os
import tempfile

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from downloader import download_media

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


def pick_dtype(url: str) -> str:
    """TikTok links get the watermark-free path; everything else is a
    plain video download (audio-only isn't offered here - add a /audio
    command later if that's wanted)."""
    return "tiktok" if "tiktok.com" in url.lower() else "video"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if not text.lower().startswith(("http://", "https://")):
        await update.message.reply_text(
            "Send me a YouTube or TikTok link and I'll send the video back "
            "(TikTok links come back with the watermark removed)."
        )
        return

    dtype = pick_dtype(text)
    status_msg = await update.message.reply_text("⏳ Downloading...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO
            )
            final_path = download_media(text, dtype, tmp_dir)
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
        with open(final_path, "rb") as f:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=f,
                caption=os.path.splitext(os.path.basename(final_path))[0][:1024],
                supports_streaming=True,
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
