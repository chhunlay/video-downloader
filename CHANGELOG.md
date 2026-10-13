# Changelog

All notable changes to this project are documented in this file, grouped by
release and ordered oldest to newest. The project has no formal version tags
in git, so versions here are inferred from the commit history to give each
meaningful batch of work its own entry.

## [0.9.2] - 2026-10-13
### Changed
- Audio quality is picked after clicking Audio, the same way Video opens
  its resolution picker, instead of sitting in the Options panel. The
  Options toggle is hidden in batch mode, where it would now be empty.

## [0.9.1] - 2026-10-13
### Fixed
- The web app no longer leaves Cloudflare tunnels running. Flask's debug
  auto-reloader started a new `cloudflared` on every code change and never
  closed the old one; the app now runs one tunnel and closes it on exit,
  including on Ctrl+C and `kill`.
- Debug mode is off, so Flask's interactive debugger is no longer
  reachable through the public link.
- The app still starts (without a public link) when `cloudflared` isn't
  installed.

## [0.9.0] - 2026-10-12
### Added
- Audio quality choice for the web app: MP3 at 128, 192 or 320 kbps, or
  M4A (cover art is embedded in both).
- Trim: optional start/end time for video, audio and TikTok downloads;
  only the chosen part is downloaded.
- Subtitles: pick a language the video offers, then save an `.srt` next
  to the video or burn the subtitles into the picture. Burned subtitles
  use complex text shaping so Khmer renders correctly, and subtitles
  line up with trimmed clips.
- Batch / playlist mode: paste up to 50 links or a playlist link and get
  one `.zip`; failed links are skipped and listed.
- Recent downloads: each browser keeps its own history with Save and
  Delete; expired downloads are marked as such.
- Job system (`app/jobs.py`): every download gets its own folder and
  progress, survives a server restart, and is cleaned up after 24 hours.
- Safety limits for the public link: at most 2 downloads run at once
  (others wait as queued), new downloads are refused when the server has
  under 2 GB free, and deleting a running download stops it and frees
  its files.
- Tests for all of the above (148 tests).

### Changed
- Files stay available for 24 hours instead of being deleted after the
  first save.
- Two people downloading at the same time no longer see each other's
  progress or files.
- The remaining emojis on the page are replaced with outline icons.
- The download buttons and options now sit in their own section, so
  batch mode works without a preview.

## [0.8.6] - 2026-10-12
### Added
- Automated tests (pytest, 54 tests) for the download helpers, the web
  app routes and the Telegram bot's text helpers. They fake yt-dlp,
  ffmpeg and the network, so they run offline in under a second.
- `requirements-dev.txt` for the test tools, and a "Running the tests"
  section in the README.

## [0.8.5] - 2026-10-12
### Changed
- The logo above the title uses a simple outline download icon instead
  of an emoji.

## [0.8.4] - 2026-10-12
### Changed
- The theme toggle (sun/moon) and the low-quality warning use simple
  outline icons instead of emojis.

## [0.8.3] - 2026-10-12
### Changed
- Download buttons (Video, Audio, TikTok, Download) use simple outline
  icons instead of emojis.

## [0.8.2] - 2026-10-11
### Added
- README header with a centered title, tagline, badges (version,
  Python, Flask, yt-dlp, python-telegram-bot, FFmpeg, last commit) and
  quick links to each section.

## [0.8.1] - 2026-10-11
### Changed
- Cleaned up code comments: short and current, with long history notes
  removed. Every Python function now has a docstring with Parameters
  and Returns sections.
- Removed duplicate code, with no change in behavior:
  - One ffprobe helper replaces five near-identical calls; the bot's
    video-dimension probe moved into `downloader.py`.
  - Temp-file swap/cleanup, TikTok link detection, the watermark
    format id and size estimates (including the bot's TikTok size)
    now live in one place in `downloader.py`.
  - `download_media` is split into small helpers for format options,
    cover-art download and cover-art embedding.
  - The bot's two button handlers share one pending-link lookup, and
    progress/size labels share one formatter each.
  - `script.js` shares helpers for showing/hiding sections, the
    resolution picker and form POSTs.

### Fixed
- Embedding mp3 cover art no longer breaks on titles containing
  quotes (ffmpeg now runs without a shell), and a failed embed no
  longer leaves a stray `final_*.mp3` file behind.
- Picking "Video" on a link with no resolution list no longer reuses
  the previous video's resolution.

## [0.8.0] - 2026-09-14
### Changed
- Resolution picker redesigned as a tappable card grid instead of a
  dropdown `<select>`, each card showing its approximate file size
  (wires up the existing `get_resolutions_with_sizes` helper, which the
  `/info` endpoint wasn't using yet).
- Removed the standalone "Best available" card; the highest resolution
  is now pre-selected by default instead.

## [0.7.0] - 2026-09-07
### Added
- YouTube resolution picker and a custom Telegram bot icon.
- Approximate file size shown on each quality button before downloading.

### Changed
- Split `index.html`'s inline CSS/JS into separate static files for
  maintainability.

## [0.6.0] - 2026-09-06
### Added
- Per-link quality choice (Low/HQ) in the Telegram bot.
- Project README.

### Changed
- Smarter re-encode skip logic (avoids re-encoding when not needed).
- Reorganized project structure for clarity (`app/`, `legacy/`, `assets/`).
- Polished Telegram bot UX.

### Fixed
- Choppy/incompatible video playback after posting downloaded clips;
  animated Telegram progress updates.

## [0.5.0] - 2026-09-05
### Added
- Telegram bot for downloading videos via chat.
- YouTube fallback source and resolution picker.
- Real cover art on downloaded audio.

### Changed
- Refactored shared download logic.
- Forced mp4 output for consistent playback.

### Fixed
- Mobile "save video" download flow.
- Focused, clearer resolution-selection step.

## [0.4.0] - 2026-09-04
### Added
- TikTok downloads with watermark removed.
- Desktop app wrapper (`desktop_app.py`) and Windows `.exe` build support.

## [0.3.0] - 2026-04-07 to 2026-04-09
### Added
- Thumbnail artwork embedded in downloaded mp3 files.
- Square thumbnail cropping for mp3 artwork.

## [0.2.0] - 2025-12-14
### Added
- Flask web app front end for pasting links and downloading.

### Changed
- Improved backend download handling.

## [0.1.0] - 2024-11-01 to 2024-11-03
### Added
- Initial YouTube video/audio (mp3) download support.
- Initial TikTok video download support.

### Changed
- Downloads normalized to mp4/mp3 output.
- Library updates and code cleanup across the early download scripts.
