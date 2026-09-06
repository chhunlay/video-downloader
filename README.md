# Video Downloader

Downloads videos from YouTube and TikTok (TikTok comes back with the
watermark removed) via a web app or a Telegram bot, both backed by the
same `downloader.py`. Downloaded video/TikTok clips are automatically
re-encoded to be safe to re-post on Instagram, TikTok, and Facebook -
fixing the choppy playback and slow uploads you get from posting a raw
downloaded file directly.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/download.html) on your system PATH
  (required for the re-encode step and for audio downloads; video
  downloads without it will just skip that step)

## Setup

```
cd web_application
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the web app

```
python app.py
```

Open **http://localhost:5050**. Paste a link, pick a type (video/audio/
TikTok) and resolution, and download.

## Running the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send
   `/newbot`, and follow the prompts to get a bot token.
2. Copy the token into a `.env` file:

   ```
   cd web_application
   cp .env.example .env
   # then edit .env and paste your token in place of the placeholder
   ```
3. Run it:

   ```
   python telegram_bot.py
   ```
4. Message your bot a YouTube or TikTok link. It'll show Low/HQ quality
   buttons, then send the video back once it's ready.

## Instagram / Facebook links

Both sites refuse most logged-out requests, so links from them only
work once you provide real session cookies:

1. Export cookies from a browser where you're logged into Instagram/
   Facebook, using an extension like *Get cookies.txt LOCALLY*
   (Netscape format).
2. Save the exported file as `web_application/cookies.txt`.

YouTube and TikTok links work without this.

## Building a standalone Windows app

See [`web_application/BUILD_WINDOWS.md`](web_application/BUILD_WINDOWS.md)
for turning the web app into a single double-clickable `.exe`
(`desktop_app.py` + PyInstaller) - no Python install needed on the
machine that runs it.

## Project layout

```
web_application/
  app.py              Flask web app
  telegram_bot.py      Telegram bot
  downloader.py         Shared yt-dlp/ffmpeg download + re-encode logic
  desktop_app.py        pywebview wrapper for the Windows build
  templates/index.html  Web app UI
tiktok_downloader.py, youtube_downloader.py, youtube_downloader_v2.py
  Early standalone scripts, kept for reference - the web app and bot
  above are the actively maintained way to use this.
```
