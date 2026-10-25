<div align="center">

# Video Downloader

**Download YouTube and TikTok videos, watermark-free and ready to re-post, from the web or Telegram.**

[![Version](https://img.shields.io/badge/version-0.14.1-blue)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-2026.8.19-FF0000?logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.10-26A5E4?logo=telegram&logoColor=white)](https://python-telegram-bot.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Last commit](https://img.shields.io/github/last-commit/chhunlay/video-downloader)](https://github.com/chhunlay/video-downloader/commits)

[Features](#features) · [Setup](#setup) · [Web app](#running-the-web-app) · [Telegram bot](#running-the-telegram-bot) · [Sites](#supported-sites) · [Tests](#running-the-tests) · [Changelog](CHANGELOG.md)

</div>

Downloads videos from YouTube and TikTok (TikTok comes back with the
watermark removed) via a web app or a Telegram bot, both backed by the
same `downloader.py`. Downloaded video/TikTok clips are automatically
re-encoded to be safe to re-post on Instagram, TikTok, and Facebook -
fixing the choppy playback and slow uploads you get from posting a raw
downloaded file directly.

## Features

Web app:

- **Video, audio or TikTok without watermark**, with a resolution picker
  showing each option's size.
- **Audio quality** - picked after clicking Audio: MP3 at 128, 192 or
  320 kbps (320 is selected by default), or M4A, with cover art.
- **Trim** - type a start and/or end time (`90`, `1:30`, `1:02:03`) and
  only that part is downloaded. YouTube trims run at full speed: a small
  local relay (`trim_relay.py`) fetches the part ffmpeg needs in chunks,
  because YouTube slows ffmpeg's own single request to playback speed.
- **Subtitles** - off unless you switch them on. Pick a language the video
  offers (plus English and Khmer auto-translations), then save it as an
  `.srt` next to the video or burn it into the picture (Khmer included).
- **Batch / playlist** - paste up to 50 links, or a playlist link, and
  get them back as one `.zip`. Links that fail are skipped and listed.
- **Live progress** - one bar for the whole download (video and audio
  together, then merging and re-encoding), with the size, speed and time
  left underneath: `38 MB of 120 MB · 4.2 MB/s · 0:35 left`.
- **Many sites** - YouTube and TikTok, plus Facebook, Instagram, X,
  SoundCloud, Dailymotion, the Internet Archive and more (see
  [Supported sites](#supported-sites)). The preview names the site.
- **Send from your phone** - open the page as `/?url=<link>` and the
  preview loads by itself, so a Share shortcut on iPhone or Android (or a
  browser bookmark) can send a video straight to it.
- **Recent downloads** - each browser keeps its own history, so you can
  save a file again or delete it. The server keeps its copy for 24 hours.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/download.html) on your system PATH
  (required for the re-encode step and for audio downloads; video
  downloads without it will just skip that step)

## Setup

```
cd app
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the web app

```
python app.py
```

Open **http://localhost:5050**. Paste a link, pick
Video or Audio and the quality, and download. Open **Options** for trim
and subtitles, or tap **Paste several links or a playlist** for a batch.
TikTok links download without the watermark from the Video button.
Finished files are saved by your browser (to `~/Downloads` by default).
The server's working copies live outside the project, in
`~/Library/Caches/Video Downloader/` on macOS (the temp folder elsewhere),
and are deleted after 24 hours.

To also get a public link, install
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
(`brew install cloudflared`) and start with:

```
python app.py --tunnel
```

It prints one `*.trycloudflare.com` address and closes it when the app
stops. The server doesn't auto-reload: restart it after changing the code.

### Sending links from your phone or browser

Open the page with a link in its address and the preview loads by itself:
`http://<your-mac>.local:5050/?url=<link>`. `?text=` works too and picks
the first link out of shared text like "Song title https://...". Find
`<your-mac>` with `scutil --get LocalHostName`; the phone must be on the
same Wi-Fi.

**iPhone (Shortcuts app):** New shortcut, then:

1. Tap the **(i)** button and turn on **Show in Share Sheet**; set it to
   receive **URLs** and **Text**.
2. Add **URL Encode** (input: *Shortcut Input*).
3. Add **Text**: `http://<your-mac>.local:5050/?text=` followed by the
   *URL Encoded Text* variable.
4. Add **Open URLs** (input: that *Text*).

Name it "Download video". In YouTube or TikTok, tap **Share → More →
Download video**.

**Android:** install **HTTP Shortcuts**, make a shortcut of type
**Browser Shortcut** with the address above ending in `?text={share}`
(turn on *Share text* under the trigger options), and pick it from
**Share** in YouTube or TikTok.

**Mac browser:** add a bookmark with this address, then click it on a
video page:

```
javascript:location='http://localhost:5050/?url='+encodeURIComponent(location.href)
```

## Running the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send
   `/newbot`, and follow the prompts to get a bot token.
2. Copy the token into a `.env` file:

   ```
   cd app
   cp .env.example .env
   # then edit .env and paste your token in place of the placeholder
   ```
3. Run it:

   ```
   python telegram_bot.py
   ```
4. Message your bot a YouTube or TikTok link. It'll show Low/HQ quality
   buttons, then send the video back once it's ready.

## Supported sites

Any site [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
supports can be tried: paste the link, and if a preview appears it works.
The preview names the site ("Vimeo · By ..."). Tested on 17 Oct 2026:

| Site | Works? | Notes |
|---|---|---|
| YouTube | Yes | Fast trims, subtitles, resolution picker |
| TikTok | Yes | No watermark |
| Facebook, Instagram | Yes | Public videos and reels; private posts need cookies |
| X (Twitter) | Yes | Posts with a video |
| Dailymotion, Twitch clips | Yes | |
| SoundCloud, Bandcamp | Yes | Use **Audio** |
| Internet Archive | Yes | Free public-domain films; a page with several videos is a playlist, so use batch mode |
| Vimeo, Reddit | With cookies | Both now need a logged-in account |
| Bilibili | Unreliable | Preview works; downloads from outside China are often cut off |
| Netflix, Disney+, Prime Video, ... | No | Copy-protected (DRM) |

A link to a whole playlist or collection asks you to use **Paste several
links or a playlist**; a link to one video inside a playlist downloads
just that video.

### Cookies for login-only sites

Vimeo, Reddit and private Instagram/Facebook posts only work with real
session cookies:

1. Export cookies from a browser where you're logged in to that site,
   using an extension like *Get cookies.txt LOCALLY* (Netscape format).
2. Save the exported file as `app/cookies.txt` and restart the app.

The file is in `.gitignore`, so it never reaches GitHub. Use a spare
account if you can: the cookies give full access to it.

## Running the tests

```
cd app
pip install -r requirements-dev.txt
python -m pytest
```

The tests cover the download helpers (formats, trim, subtitles, batch
links), the trim relay, the job store and its progress, the web app
routes and the bot's text helpers. yt-dlp, ffmpeg and YouTube are faked
(the relay tests use a fake server on localhost), so the whole suite
runs offline in a few seconds.

## Building a standalone Windows app

See [`app/BUILD_WINDOWS.md`](app/BUILD_WINDOWS.md) for turning the web
app into a single double-clickable `.exe` (`desktop_app.py` +
PyInstaller) - no Python install needed on the machine that runs it.

## Project layout

```
app/
  app.py                 Flask web app
  jobs.py                Per-download jobs: one folder + job.json each, 24 h cleanup
  version.py             App version shown in the page footer (matches CHANGELOG.md)
  telegram_bot.py        Telegram bot
  downloader.py          Shared yt-dlp/ffmpeg download + re-encode logic
  trim_relay.py          Local relay so trimmed YouTube downloads run at full speed
  desktop_app.py         pywebview wrapper for the Windows build
  templates/index.html   Web app UI
  static/                Web app script and styles
  tests/                 pytest suite (run from app/)
legacy/
  tiktok_downloader.py, youtube_downloader.py, youtube_downloader_v2.py
  Early standalone scripts, superseded by app/ - kept for reference.
```
