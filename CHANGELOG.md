# Changelog

All notable changes to this project are documented in this file, grouped by
release and ordered oldest to newest. The project has no formal version tags
in git, so versions here are inferred from the commit history to give each
meaningful batch of work its own entry.

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
