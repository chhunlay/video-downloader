# Download Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audio quality, trim, subtitles, per-browser download history and playlist/batch zip downloads to the web app, on top of a per-download job system.

**Architecture:** A new `app/jobs.py` holds a thread-safe `JobStore` (one folder + `job.json` per job, 24 h cleanup). `app/downloader.py` gains optional `audio_quality`, `trim` and `subtitles` arguments plus `expand_batch()`; defaults keep the Telegram bot unchanged. `app/app.py` replaces the global progress dict with job-scoped routes; the page (`index.html`, `script.js`, `style.css`) adds a Single/Batch switch, an Options panel and a History list.

**Tech Stack:** Python 3.13, Flask 3.1.2, yt-dlp 2026.8.19, ffmpeg (libass), pytest 9.1.1, Tailwind (CDN) + vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-12-download-features-design.md`

## Global Constraints

- Web app only; `telegram_bot.py` must keep working with no edits (all new `download_media` parameters default to today's behaviour).
- Every Python function and method gets a docstring in the project format (one-line summary, `Parameters:`, `Returns:`, `Raises:` when relevant).
- Job ids: `uuid4().hex` (32 lowercase hex chars); anything else is rejected before touching the filesystem.
- Files kept 24 h (`max_age=24*3600`), cleanup every 600 s.
- Batch limit 50 links after playlist expansion; `/jobs/lookup` accepts at most 100 ids; history keeps at most 50 entries.
- Audio quality values exactly `"128"`, `"192"` (default), `"320"`, `"m4a"`.
- Subtitle modes exactly `"srt"` and `"burn"`; subtitles ignored for audio.
- Tests are offline: fake yt-dlp, ffmpeg, network. Run from `app/` with `.venv/bin/python -m pytest`.
- One commit per changed file, Conventional Commits prefixes, no push until the user asks.
- Version bump to 0.9.0 happens once, in Task 8 (README badge commit, then CHANGELOG commit).

## Review Focus

1. **Two browsers downloading at the same time** - each sees only its own progress and files. Test: Task 5 `test_two_jobs_progress_independent`.
2. **Same title downloaded twice** - must not overwrite or serve the other job's file. Covered by per-job folders; test: Task 5 `test_files_are_scoped_to_job`.
3. **Batch with one broken link among good ones** - good items still zipped, broken one listed. Test: Task 6 `test_batch_partial_failure`.
4. **Trim end past the video's length** (e.g. `0:00`-`99:00` on a 2-minute video) - should download to the end, not error. Test: Task 2 `test_validate_trim_end_past_duration_is_ok`, plus a real-link check in Task 7 Step 4.
5. **Server restarted mid-download** - job shows as interrupted in history, not stuck "downloading" forever. Test: Task 1 `test_load_marks_running_jobs_interrupted`.

---

### Task 1: Job store

**Files:**
- Create: `app/jobs.py`
- Test: `app/tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `Job` dataclass with the spec's fields: `id, kind, dtype, options, status="queued", percent=0, title=None, files=[], error=None, item_index=0, item_count=1, failed_items=[], created=time.time()`; `Job.public() -> dict` (all fields except `options`).
  - `JobStore(root_dir: str)` with `create(kind: str, dtype: str, options: dict) -> Job`, `get(job_id: str) -> Job | None`, `update(job_id: str, **fields) -> Job | None`, `delete(job_id: str) -> bool`, `folder(job_id: str) -> str`, `load() -> None`, `cleanup(max_age: float = 24*3600, now: float | None = None) -> int` (number removed), `start_cleanup_thread(interval: float = 600) -> threading.Thread`.
  - `is_valid_job_id(job_id: str) -> bool`.

- [ ] **Step 1: Write failing tests** in `app/tests/test_jobs.py` (use `tmp_path`):
  - `test_create_makes_folder_and_json` - folder exists, `job.json` has `"status": "queued"`, `is_valid_job_id(job.id)`.
  - `test_get_rejects_bad_ids` - `store.get("../etc")`, `store.get("abc")` return `None`.
  - `test_update_persists` - after `update(id, status="done", files=["a.mp4"])`, a fresh `JobStore(tmp_path).load()` returns the same status and files.
  - `test_delete_removes_folder` - returns `True`, folder gone, `get` is `None`; second delete returns `False`.
  - `test_load_marks_running_jobs_interrupted` - job saved with `status="downloading"`, reloaded store reports `"interrupted"`.
  - `test_load_drops_broken_folders` - folder with garbage `job.json` and folder without one are deleted by `load()`.
  - `test_load_removes_old_loose_files` - a loose file in root with mtime 25 h ago is deleted; one from 1 h ago is kept.
  - `test_cleanup_removes_only_expired` - jobs created at `now-25h` and `now-1h`; `cleanup(now=now)` returns 1 and keeps the new one.

- [ ] **Step 2: Run** `cd app && .venv/bin/python -m pytest tests/test_jobs.py -v` - Expected: FAIL (`ModuleNotFoundError: jobs`).

- [ ] **Step 3: Implement `app/jobs.py`.** One `threading.Lock` around every method. `_save` writes `job.json.tmp` then `os.replace`. `load()` treats statuses other than `done`/`error`/`interrupted` as interrupted, and applies the 24 h rule to loose files (not folders) in root.

- [ ] **Step 4: Run** the same command - Expected: 8 passed.

- [ ] **Step 5: Commit** `app/tests/test_jobs.py` and `app/jobs.py` separately (`test: cover job store`, `feat: add job store with job.json persistence and 24 h cleanup`).

---

### Task 2: Audio quality and trim helpers in the downloader

**Files:**
- Modify: `app/downloader.py` (`_format_opts`, `_embed_cover_art`, `download_media`, new helpers)
- Test: `app/tests/test_downloader.py`

**Interfaces:**
- Produces:
  - `AUDIO_QUALITIES = ("128", "192", "320", "m4a")`.
  - `_format_opts(dtype, resolution, audio_quality="192") -> dict` - MP3 bitrate from `audio_quality`; `"m4a"` uses `'format': 'bestaudio[ext=m4a]/bestaudio/best'` and `preferredcodec='m4a'`. Raises `ValueError` for unknown quality.
  - `parse_timestamp(text: str | None) -> float | None` - `""`/`None` -> `None`; `"90"`, `"1:30"`, `"1:02:03"`, `"0:05.5"`; raises `ValueError("Invalid time: ...")` otherwise (including negatives, `"1:75"`).
  - `validate_trim(start: float | None, end: float | None, duration: float | None) -> tuple | None` - returns `None` when both are `None`; raises `ValueError` when `end <= start` or `start >= duration`; clamps `end` to `duration` when it is past it.
  - `download_media(..., audio_quality="192", trim=None, ...)` - `trim` is a `(start, end)` tuple already validated; sets `download_ranges=yt_dlp.utils.download_range_func(None, [(start or 0, end or float("inf"))])` and `force_keyframes_at_cuts=True`. Final extension is `.m4a` for `audio_quality="m4a"`.
  - `_embed_cover_art(audio_path, thumb_path)` - for `.m4a` uses `-disposition:v:0 attached_pic` instead of the id3v2 flags.

- [ ] **Step 1: Write failing tests:**
  - `test_format_opts_audio_bitrates` - parametrize `"128"`, `"192"`, `"320"`: `postprocessors[0]["preferredquality"] == q` and codec `"mp3"`.
  - `test_format_opts_audio_m4a` - codec `"m4a"`, `"m4a" in opts["format"]`.
  - `test_format_opts_audio_rejects_unknown` - `pytest.raises(ValueError)` for `"999"`.
  - `test_parse_timestamp` - parametrize `("", None), ("90", 90), ("1:30", 90), ("1:02:03", 3723), ("0:05.5", 5.5)`.
  - `test_parse_timestamp_invalid` - parametrize `"abc", "-5", "1:75", "1::2"` raise `ValueError`.
  - `test_validate_trim` - `(30, 75, 120) -> (30, 75)`; `(None, None, 120) -> None`; `(None, 60, None) -> (None, 60)`.
  - `test_validate_trim_rejects` - `(75, 30, 120)`, `(30, 30, 120)`, `(130, None, 120)` raise `ValueError`.
  - `test_validate_trim_end_past_duration_is_ok` - `(0, 5940, 120) -> (0, 120)`.
  - `test_download_media_passes_trim_to_ytdlp` - monkeypatch `downloader.extract_info_with_fallback` to capture `ydl_opts` and return `({"title": "t", "ext": "mp4"}, opts)`, patch `normalize_for_social` to a no-op; call with `trim=(30, 75)` and dtype `"video"`; assert `"download_ranges" in captured` and `captured["force_keyframes_at_cuts"] is True`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_downloader.py -v` - Expected: new tests FAIL.

- [ ] **Step 3: Implement** the helpers and parameters above in `app/downloader.py`. Pass `audio_quality` from `download_media` into `_format_opts`.

- [ ] **Step 4: Run** the whole suite - Expected: all pass (existing tests unchanged).

- [ ] **Step 5: Commit** test file and `downloader.py` separately (`test: cover audio quality and trim helpers`, `feat: add audio quality and trim options to download_media`).

---

### Task 3: Subtitles in the downloader

**Files:**
- Modify: `app/downloader.py` (`download_media`, `normalize_for_social`, new helpers)
- Test: `app/tests/test_downloader.py`

**Interfaces:**
- Consumes: `download_media` signature from Task 2.
- Produces:
  - `get_subtitle_languages(info: dict) -> list[dict]` - items `{"code", "name", "auto"}`; manual tracks from `info["subtitles"]` first, then auto tracks from `info["automatic_captions"]` whose code is the original language (`info.get("language")`, or codes ending in `-orig`); skip `live_chat`; each group sorted by `name`; `name` from the track's first entry `name` field, falling back to the code.
  - `subtitle_path_for(video_path: str, lang: str) -> str` - `"<video without ext>.<lang>.srt"`.
  - `download_media(..., subtitles=None)` - `subtitles={"lang": str, "mode": "srt"|"burn"}`; ignored for `dtype == "audio"`; adds `writesubtitles=True`, `writeautomaticsub=True`, `subtitleslangs=[lang]`, and postprocessor `{"key": "FFmpegSubtitlesConvertor", "format": "srt"}`. For `burn`: if `subtitle_path_for(...)` does not exist after download, raise `ValueError("No subtitles found for that language")`; otherwise pass it to `normalize_for_social(..., burn_subtitles=path)` and delete it afterwards.
  - `normalize_for_social(path, progress_hook=None, max_height=None, burn_subtitles=None)` - `burn_subtitles` forces the re-encode branch; the `-vf` value joins the scale filter and `subtitles=<escaped path>` with a comma. Escape `\`, `:`, `'` in the path for the filter.

- [ ] **Step 1: Write failing tests:**
  - `test_get_subtitle_languages` - info with `subtitles={"km": [{"name": "Khmer"}], "en": [{"name": "English"}], "live_chat": [{}]}`, `automatic_captions={"en": [{"name": "English"}], "fr": [{"name": "French"}]}`, `language="en"` -> `[{"code":"en","name":"English","auto":False}, {"code":"km","name":"Khmer","auto":False}, {"code":"en","name":"English","auto":True}]`.
  - `test_get_subtitle_languages_empty` - `{}` -> `[]`.
  - `test_subtitle_path_for` - `"/d/Clip.mp4", "km"` -> `"/d/Clip.km.srt"`.
  - `test_download_media_subtitle_opts` - same capture approach as Task 2; `subtitles={"lang": "km", "mode": "srt"}` -> `captured["subtitleslangs"] == ["km"]`, `captured["writesubtitles"] is True`, `"FFmpegSubtitlesConvertor"` among postprocessor keys.
  - `test_download_media_audio_ignores_subtitles` - dtype `"audio"` with subtitles -> `"subtitleslangs" not in captured` (also patch `_fetch_cover_art` to return `None`).
  - `test_burn_without_subtitle_file_fails` - mode `"burn"`, no `.srt` created -> `pytest.raises(ValueError, match="No subtitles")`.
  - `test_burn_passes_subtitle_to_normalize` - create the expected `.srt` in `tmp_path` from the fake extractor; patched `normalize_for_social` records `burn_subtitles`; assert it equals `subtitle_path_for(...)` and the `.srt` is deleted afterwards.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_downloader.py -v` - Expected: new tests FAIL.

- [ ] **Step 3: Implement** the helpers and changes above.

- [ ] **Step 4: Run** whole suite - Expected: all pass.

- [ ] **Step 5: Manual ffmpeg check** - burn a 2-line Khmer `.srt` into any short local mp4 via `normalize_for_social(path, burn_subtitles=srt)`; open the output and confirm the Khmer text renders (not boxes). If boxes appear, add `force_style='FontName=Khmer Sangam MN'` to the filter.

- [ ] **Step 6: Commit** test file and `downloader.py` separately (`test: cover subtitle options`, `feat: add subtitle download and burn-in`).

---

### Task 4: Batch link expansion

**Files:**
- Modify: `app/downloader.py`
- Test: `app/tests/test_downloader.py`

**Interfaces:**
- Produces: `BATCH_LIMIT = 50`; `expand_batch(text: str, limit: int = BATCH_LIMIT, extract=None) -> list[str]`. `extract(url) -> dict` defaults to a flat yt-dlp extraction (`extract_flat="in_playlist"`, quiet, cookie opts); it is only called for entries whose URL contains `list=` or `/playlist`. Playlist entries map to `entry["url"]` (or `entry["webpage_url"]`). Raises `ValueError("No links found")` and `ValueError("Too many links: N (max 50)")`.

- [ ] **Step 1: Write failing tests:**
  - `test_expand_batch_splits_and_dedupes` - `"https://a\n\nhttps://b  https://a\nnot-a-link"` -> `["https://a", "https://b"]`.
  - `test_expand_batch_expands_playlists` - fake `extract` returns `{"entries": [{"url": "https://v1"}, {"url": "https://v2"}]}` for `"https://youtube.com/playlist?list=X"`; text `"https://v0\nhttps://youtube.com/playlist?list=X"` -> `["https://v0", "https://v1", "https://v2"]`.
  - `test_expand_batch_empty` - `"hello"` raises `ValueError("No links found")`.
  - `test_expand_batch_limit` - 51 distinct links raise `ValueError` matching `"max 50"`; 50 pass.

- [ ] **Step 2: Run** - Expected: FAIL.
- [ ] **Step 3: Implement** `expand_batch`.
- [ ] **Step 4: Run** whole suite - Expected: all pass.
- [ ] **Step 5: Commit** separately (`test: cover batch link expansion`, `feat: add expand_batch for multi-link and playlist input`).

---

### Task 5: Job-based routes and single downloads

**Files:**
- Modify: `app/app.py` (replace `progress_data`, `progress_hook`, `download_task`, `/progress`, `/downloads/<name>`)
- Test: `app/tests/test_app.py` (rewrite tests for removed routes)

**Interfaces:**
- Consumes: `JobStore`, `is_valid_job_id` (Task 1); `download_media`, `parse_timestamp`, `validate_trim`, `AUDIO_QUALITIES`, `subtitle_path_for`, `get_subtitle_languages`, `get_video_info` (Tasks 2-3).
- Produces (module level in `app.py`):
  - `store = JobStore(DOWNLOAD_DIR)`; `store.load()` and `store.start_cleanup_thread()` run under `if __name__ == "__main__":` only (tests construct their own store via monkeypatch).
  - `parse_download_form(form) -> dict` - returns `{"mode", "dtype", "url" or "urls", "options"}`; options keys `resolution`, `audio_quality`, `trim`, `subtitles`. Raises `ValueError` with a user-facing message. Single-mode trim validation uses `duration=None` (the worker clamps with the real duration).
  - `make_job_hook(job_id: str) -> callable` - maps yt-dlp/normalize dicts to `store.update` with statuses `downloading`, `processing`, `optimizing`; only writes when status or whole percent changes.
  - `run_single(job_id: str, url: str) -> None` - worker.
  - `clean_error(e: Exception) -> str` - strips a leading `ERROR: ` and `[site] id: ` prefix.
  - Routes exactly as in the spec table; `POST /download` returns `{"job_id"}` or `400 {"error"}`; `/progress/<id>` SSE payload is `Job.public()`.

- [ ] **Step 1: Rewrite `app/tests/test_app.py`.** Fixture `client` monkeypatches `webapp.store = JobStore(tmp_path)` and replaces `webapp.threading.Thread` with the existing `InlineThread` pattern so workers run synchronously. Tests:
  - keep `test_index_page`; update `test_info_returns_preview` to also assert `data["subtitles"] == []`.
  - `test_download_single_success` - fake `download_media` writes `clip.mp4` into `out_dir`; POST returns a `job_id`; `store.get(id).status == "done"`, `files == ["clip.mp4"]`.
  - `test_download_passes_options` - form with `audio_quality=320`, `trim_start=0:30`, `trim_end=1:15`, `sub_lang=km`, `sub_mode=srt`, type `video`; fake records kwargs: `trim == (30, 75)`, `audio_quality == "320"`, `subtitles == {"lang": "km", "mode": "srt"}`.
  - `test_download_includes_srt_file` - fake also writes `clip.km.srt`; `files == ["clip.mp4", "clip.km.srt"]`.
  - `test_download_rejects_bad_input` - parametrize: `trim_start=abc`; `trim_start=2:00, trim_end=1:00`; `audio_quality=999`; `sub_mode=xyz` with `sub_lang=km` -> status 400 with `"error"` key, no folders created in `tmp_path`.
  - `test_download_failure_sets_error` - fake raises `DownloadError("ERROR: [youtube] abc: Video unavailable")` -> job `status == "error"`, `error == "Video unavailable"`.
  - `test_progress_stream_for_job` - done job streams one event with its `files`.
  - `test_progress_unknown_job_404`.
  - `test_files_are_scoped_to_job` - two jobs each with `clip.mp4` of different bytes; each `/files/<id>/clip.mp4` returns its own bytes; files still exist after serving.
  - `test_files_blocks_traversal` - `/files/<id>/../<other id>/clip.mp4` is 404.
  - `test_two_jobs_progress_independent` - update job A to 40 % downloading and B to `done`; `/progress/<A>` first event has `percent == 40`.
  - `test_jobs_lookup` - known id returns its public dict, unknown valid id and invalid id return `null`; more than 100 ids -> 400.
  - `test_delete_job` - 204 then 404; folder gone.
  - keep `test_progress_hook_states` rewritten against `make_job_hook`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_app.py -v` - Expected: FAIL.

- [ ] **Step 3: Implement** in `app/app.py`. `run_single` sets `title` from the downloaded file name stem and passes the parsed trim tuple straight to `download_media`; no extra info lookup is made, because yt-dlp's range download stops at the real end of the video. Files list = main file name, plus `subtitle_path_for` result if it exists. `/info` adds `"subtitles": get_subtitle_languages(info)`.

- [ ] **Step 4: Run** the whole suite - Expected: all pass.

- [ ] **Step 5: Commit** test file and `app.py` separately (`test: cover job-based routes`, `feat: replace global progress with per-job routes`).

---

### Task 6: Batch worker and zip

**Files:**
- Modify: `app/app.py`
- Test: `app/tests/test_app.py`

**Interfaces:**
- Consumes: `expand_batch` (Task 4), `store`, `parse_download_form`, `make_job_hook` (Task 5).
- Produces: `run_batch(job_id: str, urls: list[str]) -> None`. `parse_download_form` in batch mode calls `expand_batch(form["urls"])`, ignores trim/subtitle fields, and keeps `resolution` and `audio_quality`. Job title `"Batch (N items)"`; zip name `batch-<first 8 chars of id>.zip`.

- [ ] **Step 1: Write failing tests:**
  - `test_batch_zips_all_items` - three links, fake `download_media` writes `<n>.mp4`; job `done`, `files == ["batch-xxxxxxxx.zip"]`, zip namelist has the three files, loose `.mp4` files are gone, `item_count == 3`.
  - `test_batch_partial_failure` - second link raises; job `done`, zip has 2 files, `failed_items == [{"url": "https://b", "error": "..."}]`.
  - `test_batch_all_fail` - every link raises; job `status == "error"`.
  - `test_batch_too_many_links` - 51 links -> 400.
  - `test_batch_progress_overall` - hook calls inside the fake at 50 % for item 2 of 4 lead to a recorded percent of 37 (`(1 + 0.5) / 4 * 100`, int).

- [ ] **Step 2: Run** - Expected: FAIL.
- [ ] **Step 3: Implement** `run_batch` (sequential, `zipfile.ZIP_STORED`, skip `thumb_temp.jpg` and `job.json` when zipping).
- [ ] **Step 4: Run** whole suite - Expected: all pass.
- [ ] **Step 5: Commit** separately (`test: cover batch downloads`, `feat: add batch downloads zipped into one file`).

---

### Task 7: Web page - options, batch, progress, history, icons

**Files:**
- Modify: `app/templates/index.html`, `app/static/script.js`, `app/static/style.css`

**Interfaces:**
- Consumes: routes from Tasks 5-6. `/info` returns `subtitles: [{code, name, auto}]`; `/download` form fields `mode, url, urls, type, resolution, audio_quality, trim_start, trim_end, sub_lang, sub_mode`; `/progress/<id>` events are `Job.public()`.
- Produces (in `script.js`): `HISTORY_KEY = 'video-downloader-history'`; `loadHistory() -> Array`, `saveHistory(list)`, `addToHistory(entry)`, `renderHistory()`, `refreshHistory()` (calls `/jobs/lookup`), `saveJobFiles(job)`, `trackJob(jobId)` (EventSource on `/progress/<id>`), `setMode('single'|'batch')`.

- [ ] **Step 1: Markup** - Single/Batch segmented control; batch `<textarea id="urls">` with counter `#linkCount` ("N links (max 50)"); collapsed Options panel with `#trimStart`, `#trimEnd`, audio chips `data-quality` (128/192/320/M4A, 192 selected), `#subLang` select + chips Off/.srt file/Burn in, wrapped in `#subtitleOptions` (hidden when no languages); batch resolution choices 1080/720/480/360 reuse `renderResolutions([[1080,null],[720,null],[480,null],[360,null]])`; `#batchCounter` beside status; `#failedItems` list; History section `#history` with note "Files are kept for 24 hours."; replace 😢 🎉 ✅ ❌ with outline SVGs (same attribute style as existing icons; status icons via a small `statusIcon(name)` returning SVG markup).

- [ ] **Step 2: Script** - `startDownload` posts the form, shows a 400 `error` in `#errorMessage`, otherwise `addToHistory({id, title, dtype, kind, created})` then `trackJob`. On `done`, `saveJobFiles` triggers one download per file via `/files/<id>/<name>`. All `localStorage` access in try/catch. History rows: title, type icon, relative time ("5 min ago"), Save / Delete; unknown ids "Expired" with remove; running jobs show status. Escape titles when inserting HTML (use `textContent`).

- [ ] **Step 3: Styles** - chips and segmented control reuse `res-card` look; light and dark tokens already in `style.css`; layout holds at 375 px width.

- [ ] **Step 4: Manual check** - restart the server, then in the browser: plain video; audio at 320 and M4A; trimmed video `0:10`-`0:20` (check length) and `0:00`-`99:00` on a short video (downloads to the end); `.srt` and burn-in on a video with Khmer or English subtitles; batch of 3 links with one bad; a short playlist; refresh and see history; Save again; Delete; Expired row (delete a job folder by hand); light/dark; phone width. Note anything that fails and fix before committing.

- [ ] **Step 5: Commit** `index.html`, `script.js`, `style.css` separately (`feat: add options panel, batch mode and history to the page`, `feat: track jobs, history and options in script.js`, `style: add chip, segmented control and history styles`).

---

### Task 8: Docs and version 0.9.0

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `app/.gitignore` (only if new temp files need ignoring)

- [ ] **Step 1: README** - add a "Features" list to the existing content describing the five features and the 24 h retention; add `jobs.py` to "Project layout"; update the test count in "Running the tests" if mentioned.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q` - Expected: all pass; note the count for the changelog.
- [ ] **Step 3: Commit** README docs change (`docs: describe new download features`), then bump the badge to `version-0.9.0-blue` and commit it on its own (`chore: bump version badge to 0.9.0`); check the badge URL returns an image.
- [ ] **Step 4: CHANGELOG** - `## [0.9.0] - <date>` with `### Added` (audio quality, trim, subtitles, history, batch/playlist zip, job system, tests) and `### Changed` (files kept 24 h instead of deleted on first save; two visitors no longer share progress; remaining emojis replaced with icons). Commit `docs: add 0.9.0 changelog entry`.
