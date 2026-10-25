# Changelog

All notable changes to this project are documented in this file, grouped by
release and ordered oldest to newest. The project has no formal version tags
in git, so versions here are inferred from the commit history to give each
meaningful batch of work its own entry.

## [0.14.1] - 2026-10-25
### Removed
- The Save and Delete buttons in "Recent downloads". Finished files are
  still kept for 24 hours; expired entries keep their Remove button.

## [0.14.0] - 2026-10-17
### Added
- The preview names the site a link is from ("Vimeo · By ...").
- A "Supported sites" table in the README, from real tests of 12 sites.

### Changed
- Sites that need an account (Vimeo, Reddit) now say "This site needs
  you to be logged in" instead of "Video not found", and X posts
  without a video say so.

### Fixed
- A link to a video inside a playlist or mix (`watch?v=...&list=...`)
  downloads only that video, not the whole playlist.
- A link that is only a playlist or collection (e.g. an Internet
  Archive page with 10 videos) no longer downloads every video as if it
  were one; the preview points to batch mode instead.

## [0.13.0] - 2026-10-17
### Added
- Opening the page as `/?url=<link>` (or `?text=` with a link inside)
  fills in the link and loads its preview, then clears it from the
  address bar. The README shows how to set up a Share shortcut on
  iPhone and Android, and a bookmark for desktop browsers.

## [0.12.1] - 2026-10-17
### Changed
- The progress bar is solid indigo while downloading, then turns green
  when done or red when it fails, instead of an indigo-to-pink
  gradient. The percent takes the same color.

## [0.12.0] - 2026-10-17
### Added
- A detail line under the progress bar while downloading:
  "38 MB of 120 MB · 4.2 MB/s · 0:35 left". Parts that aren't known
  are left out, and the bar slides while the size is still unknown.

### Changed
- One bar for the whole download. Video and audio are filled by their
  sizes, so the bar no longer drops back to 0% when the audio starts;
  downloading fills 0-90%, merging and re-encoding the rest.
- Trimmed YouTube downloads are much faster (a 3:34 clip of a 6-hour
  video: about 1:48 before, a few seconds now). YouTube slows ffmpeg's
  single long request to playback speed, so ffmpeg now reads through a
  local relay that fetches in 10 MB chunks, like yt-dlp does. If the
  relay fails, the download is redone the old way.

### Fixed
- Trimmed downloads showed 0% the whole time; they now show progress.
- Subtitle files, fetched before the video, no longer move the bar.

## [0.11.1] - 2026-10-17
### Removed
- The Paste button in the link field. Links are pasted the usual way
  (long-press or Cmd/Ctrl+V); the Clear button stays.

## [0.11.0] - 2026-10-13
### Changed
- Phone layout: the thumbnail keeps the video's real shape (16:9, or a
  height-capped portrait for TikTok/Shorts) with a duration badge, and
  the header is smaller, so the download buttons show without scrolling.
- Video is the main button and Audio a secondary outlined one. A single
  TikTok link downloads without the watermark from the Video button (a
  note says so); the separate TikTok button now only appears in batch mode.
- Batch mode is opened with "Paste several links or a playlist" under the
  link field instead of a Single/Batch switch.
- Errors are shown in plain words ("This video is private", "This site
  isn't supported", ...) instead of raw yt-dlp messages.
- Labels use one style; the trim End field shows the video's length.

### Added
- Paste and Clear buttons in the link field.
- A summary on the Options row ("0:30–1:15 · Khmer .srt").
- A Cancel button while downloading.

### Fixed
- Accessibility: footer and hint text now meet WCAG AA contrast, every
  button is at least 44px tall, and the thumbnail has alt text.

## [0.10.4] - 2026-10-13
### Added
- The app version is shown in the page footer. It comes from
  `app/version.py`, and a test checks it matches the newest CHANGELOG
  entry.

## [0.10.3] - 2026-10-13
### Fixed
- YouTube downloads no longer fail straight away on "HTTP Error 403:
  Forbidden". The request is retried, and if YouTube keeps refusing, the
  download switches to YouTube's Android client (as it already did for
  "sign in to confirm" errors).

## [0.10.2] - 2026-10-13
### Changed
- The audio quality picker selects 320 kbps (the highest) by default
  instead of 192 kbps.

### Removed
- The "Save File" button under a finished download. The browser's own
  download popup does the job; History's Save button still re-downloads.

## [0.10.1] - 2026-10-13
### Fixed
- The browser's download popup is back. 0.10.0 moved downloads made on
  this computer into `~/Downloads` itself and skipped the browser
  download; now every download goes through the browser again, which
  saves it to `~/Downloads`.

### Changed
- The server's working copies moved out of the project, from
  `app/downloads/` to `~/Library/Caches/Video Downloader/` on macOS (the
  temp folder elsewhere); they are still deleted after 24 hours.

## [0.10.0] - 2026-10-13
### Added
- Downloads made on this computer are saved straight into `~/Downloads`
  and kept there; existing files are never overwritten (`Title (1).mp4`)
  and the browser no longer saves a second copy. Downloads from other
  devices still go through the browser and stay on the server for 24 h.
- English and Khmer auto-translated subtitles are offered for every video
  that has auto captions.

### Changed
- Subtitles are off unless switched on; the switch resets for each new
  video and reveals the language and .srt / Burn in choices.
- The public Cloudflare link only opens with `python app.py --tunnel`.
- The Video button uses the play icon; TikTok uses a phone icon.

### Fixed
- Subtitle languages showed codes like "en" instead of "English" when
  YouTube's first caption format had no name.

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
